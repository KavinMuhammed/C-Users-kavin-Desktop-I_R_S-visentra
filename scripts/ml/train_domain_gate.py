"""Train and package the aircraft-surface domain gate (Phase 13).

    python scripts/ml/train_domain_gate.py --gate-id VT-GATE-0001 [--max-train-positives 1600]

Positives: aircraft-surface images of the prepared defect datasets (VT-PREP-001; one file per photo group so
augmented copies are not over-counted) and the ImageNet aircraft samples. Negatives: VT-NEG-001 (ImageNet,
non-aircraft) and VT-NEG-002 (generated documents, charts, interfaces, plain images). Every source keeps its own
train/valid/test split; the threshold is chosen on valid (aircraft recall ≥ --min-recall) and the test split is
scored once, at the end. Writes models/gate/<id>/{coco.pth, gate.json, evaluation.json, SHA256SUMS}.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import domain_gate as G  # noqa: E402

COCO = ROOT / "models" / "pretrained" / "yolox_tiny_coco_0.1.1rc0.pth"
COCO_SHA = "9de513de589ac98bb92d3bca53b5af7b9acfa9b0bacb831f7999d0f7afaee8f0"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def positives(split: str, cap: int | None, rng: random.Random) -> list[tuple[str, str]]:
    rows = list(csv.DictReader(open(ROOT / f"datasets/splits/VT-PREP-001/{split}.csv", encoding="utf-8")))
    if split == "test":
        rows = [r for r in rows if r.get("eval_representative") == "yes"]
    by = {}
    for r in sorted(rows, key=lambda r: r["file_id"]):
        by.setdefault((r["dataset_id"], r["photo_group"]), r)
    pick = list(by.values())
    rng.shuffle(pick)
    if cap:
        ds2 = [r for r in pick if r["dataset_id"] == "VT-DS-002"]
        ds1 = [r for r in pick if r["dataset_id"] != "VT-DS-002"]
        pick = ds2[: cap // 3] + ds1[: cap - min(len(ds2), cap // 3)]
    return [(r["rel_path"], r["dataset_id"]) for r in pick]


def negatives(split: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    neg, pos = [], []
    for ds in ("VT-NEG-001", "VT-NEG-002"):
        for r in csv.DictReader(open(ROOT / f"datasets/negatives/{ds}/manifest.csv", encoding="utf-8")):
            if r["split"] != split:
                continue
            if r["aircraft"] == "no":
                neg.append((r["file"], f"{ds}:{r['label']}"))
            elif r["aircraft"] == "yes":
                pos.append((r["file"], f"{ds}:{r['label']}"))
    return neg, pos


def feats(fx: G.FeatureExtractor, files: list[str], batch: int = 8) -> np.ndarray:
    out = []
    for i in range(0, len(files), batch):
        ims = []
        for f in files[i:i + batch]:
            with Image.open(ROOT / f) as im:
                im.load()
                ims.append(im.convert("RGB"))
        out.append(fx.features(ims))
        if (i // batch) % 25 == 0:
            print(f"  features {i + len(ims)}/{len(files)}", flush=True)
    return np.concatenate(out)


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 2.0, iters: int = 50) -> tuple[np.ndarray, float]:
    """L2-regularised logistic regression with balanced class weights, fitted by Newton's method (IRLS). Deterministic."""
    n, d = X.shape
    cw = np.where(y == 1, n / (2 * max(1, y.sum())), n / (2 * max(1, (y == 0).sum())))
    A = np.hstack([X, np.ones((n, 1))])
    beta = np.zeros(d + 1)
    reg = np.full(d + 1, l2); reg[-1] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(A @ beta, -40, 40)))
        g = A.T @ (cw * (p - y)) + reg * beta
        H = (A * (cw * p * (1 - p))[:, None]).T @ A + np.diag(reg + 1e-6)
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-7:
            break
    return beta[:-1], float(beta[-1])


def metrics(p: np.ndarray, airplane: np.ndarray, y: np.ndarray, t: float, amin: float) -> dict:
    acc = (p >= t) | (airplane >= amin)
    tp = int((acc & (y == 1)).sum()); fn = int((~acc & (y == 1)).sum()); fp = int((acc & (y == 0)).sum()); tn = int((~acc & (y == 0)).sum())
    return {"aircraft_images": int((y == 1).sum()), "other_images": int((y == 0).sum()), "aircraft_accepted": tp, "aircraft_rejected": fn,
            "other_rejected": tn, "other_accepted": fp, "aircraft_recall": tp / max(1, tp + fn), "other_rejection_rate": tn / max(1, tn + fp)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate-id", default="VT-GATE-0001")
    ap.add_argument("--max-train-positives", type=int, default=1600)
    ap.add_argument("--min-recall", type=float, default=0.98)
    ap.add_argument("--airplane-min-score", type=float, default=0.5)
    ap.add_argument("--threads", type=int, default=1)
    a = ap.parse_args()
    out = ROOT / "models" / "gate" / a.gate_id
    if out.exists():
        print(f"{out} exists — gate versions are immutable; use a new id"); return 2
    if sha(COCO) != COCO_SHA:
        print("COCO weights checksum mismatch"); return 2
    rng = random.Random(20261017)
    fx = G.FeatureExtractor(COCO, threads=a.threads)
    data = {}
    t0 = time.time()
    for split in ("train", "valid", "test"):
        pos = positives(split, a.max_train_positives if split == "train" else None, rng)
        neg, pos_extra = negatives(split)
        files = [f for f, _ in pos + pos_extra + neg]
        y = np.array([1] * (len(pos) + len(pos_extra)) + [0] * len(neg))
        src = [s for _, s in pos + pos_extra + neg]
        print(f"{split}: {int(y.sum())} aircraft, {int((y == 0).sum())} other", flush=True)
        data[split] = (feats(fx, files), y, src, files)
    Xtr, ytr, _, _ = data["train"]
    mean, std = Xtr.mean(0), Xtr.std(0) + 1e-6
    w, b = fit_logistic((Xtr - mean) / std, ytr, l2=2.0)

    def prob(X):
        return 1 / (1 + np.exp(-np.clip(((X - mean) / std) @ w + b, -40, 40)))

    Xv, yv, _, _ = data["valid"]
    pv, av = prob(Xv), Xv[:, -80 + G.COCO_AIRPLANE]
    # threshold chosen on valid only: maximise aircraft recall + other-image rejection (Youden's J); among equally good
    # thresholds take the one closest to 0.5 so neither error type is favoured; recall must stay ≥ --min-recall
    grid = [float(t) for t in np.round(np.arange(0.05, 0.96, 0.01), 2)]
    def score(t):
        m = metrics(pv, av, yv, t, a.airplane_min_score)
        return (m["aircraft_recall"] >= a.min_recall, round(m["aircraft_recall"] + m["other_rejection_rate"], 6), -abs(t - 0.5))
    best = max(grid, key=score)
    valid = metrics(pv, av, yv, best, a.airplane_min_score)
    Xt, yt, st, ft = data["test"]
    pt, at = prob(Xt), Xt[:, -80 + G.COCO_AIRPLANE]
    test = metrics(pt, at, yt, best, a.airplane_min_score)
    acc = (pt >= best) | (at >= a.airplane_min_score)
    errors = [{"file": f, "source": s, "aircraft": int(y), "p": round(float(p), 4)} for f, s, y, p, ok in zip(ft, st, yt, pt, acc) if bool(ok) != bool(y)]
    out.mkdir(parents=True)
    shutil.copyfile(COCO, out / "coco.pth")
    spec = {"schema": "visiontra.domain_gate/1", "gate_id": a.gate_id, "feature_version": G.FEATURE_VERSION, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "coco_weights": {"file": "coco.pth", "sha256": COCO_SHA, "source": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.pth",
                             "licence": "Apache-2.0"},
            "classifier": "L2-regularised logistic regression (lambda=2, balanced classes, Newton/IRLS) on standardised features", "threshold": best, "airplane_min_score": a.airplane_min_score,
            "threshold_rule": f"validation split only: maximise aircraft recall + other-image rejection, ties → closest to 0.5; aircraft recall ≥ {a.min_recall} (airplane rule included)",
            "mean": mean.tolist(), "std": std.tolist(), "weights": w.tolist(), "bias": b,
            "training_data": {"positives": "VT-PREP-001 aircraft-surface images (VT-DS-001, VT-DS-002; one file per photo group) + ImageNet airliner/warplane/wing samples",
                              "negatives": "VT-NEG-001 ImageNet samples (non-aircraft classes; non-commercial research terms) + VT-NEG-002 generated documents/charts/interfaces/plain images (CC0)",
                              "counts": {s: {"aircraft": int(d[1].sum()), "other": int((d[1] == 0).sum())} for s, d in data.items()}}}
    (out / "gate.json").write_text(json.dumps(spec))
    (out / "evaluation.json").write_text(json.dumps({"valid": valid, "test": test, "test_errors": errors, "feature_seconds": round(time.time() - t0, 1)}, indent=1))
    sums = {p.name: sha(p) for p in sorted(out.iterdir())}
    (out / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in sums.items()))
    print(json.dumps({"threshold": best, "valid": valid, "test": test, "test_errors": len(errors)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
