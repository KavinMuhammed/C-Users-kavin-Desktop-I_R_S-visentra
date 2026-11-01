"""Postprocessing study on the VALIDATION side (Phase 2, 29 Oct): same-class containment suppression after NMS.

    python scripts/ml/postprocess_study.py [--bundle models/detection/VT-DET-0002] [--out experiments/phase2/postprocess_study.json]

Error analysis (scripts/ml/error_analysis.py) showed nested same-class boxes of very different sizes, which IoU-based NMS
(0.65) keeps. Variant "contain@c": after NMS, a box is dropped when a higher-scoring box of the same class covers at least
c of the smaller box's area (intersection / min area). Operating thresholds are NOT re-tuned; only the variant changes.
Metrics use the repository's evaluator (engine.evaluate_preds). Criteria: experiments/phase2/ACCEPTANCE_CRITERIA.md.
Never reads the test split.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
from visentra_ml.detection import engine as E  # noqa: E402
import evaluate_hard_cases as H  # noqa: E402


def contain_suppress(preds, c):
    """preds: [(cls, score, xyxy)] → same list without boxes mostly covered by (or covering) a higher-scoring same-class box."""
    keep = []
    for p in sorted(preds, key=lambda x: -x[1]):
        ok = True
        for q in keep:
            if q[0] != p[0]:
                continue
            a, b = p[2], q[2]
            iw, ih = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            mn = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
            if mn > 0 and iw * ih / mn >= c:
                ok = False; break
        if ok:
            keep.append(p)
    return keep


def f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def prf(gts, preds, classes, th, miou):
    """tp/fp/fn per class at thresholds (greedy, same rule as the evaluator) for the bootstrap."""
    out = {c: [0, 0, 0] for c in classes}
    for k in gts:
        for ci, c in enumerate(classes):
            g = [b for cc, b in gts[k] if cc == ci]
            p = sorted([x for x in preds[k] if x[0] == ci and x[1] >= th[c]], key=lambda x: -x[1])
            used = set()
            for _, s, b in p:
                best, bi = 0, None
                for j, gb in enumerate(g):
                    if j in used:
                        continue
                    v = H.iou(b, gb)
                    if v > best:
                        best, bi = v, j
                if bi is not None and best >= miou:
                    used.add(bi); out[c][0] += 1
                else:
                    out[c][1] += 1
            out[c][2] += len(g) - len(used)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002")
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json")
    ap.add_argument("--cache", default="experiments/phase2/error_analysis/preds.json")
    ap.add_argument("--out", default="experiments/phase2/postprocess_study.json")
    ap.add_argument("--variants", default="0.6,0.7,0.8,0.9")
    a = ap.parse_args()
    meta = json.loads((ROOT / a.bundle / "metadata.json").read_text())
    classes, th, miou = meta["classes"], meta["thresholds"]["score_threshold"], meta["thresholds"]["match_iou"]
    cache = json.loads((ROOT / a.cache).read_text())
    assert cache["bundle_sha256"] == meta["files"]["model.pt"]["sha256"] and cache["split"] == "valid"
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg)
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    gts, sizes = {}, {}
    for it in items:
        with Image.open(C.resolve(it.rel_path)) as im:
            W, H_ = ImageOps.exif_transpose(im).size
        sizes[it.file_id] = (W, H_); gts[it.file_id] = [(b.cls, [b.x1 * W, b.y1 * H_, b.x2 * W, b.y2 * H_]) for b in it.boxes]
    base = {k: [(c, s, b) for c, s, b in v] for k, v in cache["preds"].items()}
    # negatives (validation side): predicted once and cached
    negc = ROOT / "experiments/phase2/neg_preds.json"
    if negc.exists():
        neg = json.loads(negc.read_text())
    else:
        import torch
        P = H.load_predictor(ROOT / a.bundle, None)
        if torch.cuda.is_available():
            P.model = P.model.cuda(); _f = P.model.forward
            P.model.forward = lambda x, *r, **k: _f(x.cuda(), *r, **k).cpu()
        rows = {"VT-NEG valid (non-aircraft)": [r["file"] for m in ("datasets/negatives/VT-NEG-001/manifest.csv", "datasets/negatives/VT-NEG-002/manifest.csv")
                                                for r in csv.DictReader(open(ROOT / m, encoding="utf-8")) if r["split"] == "valid" and r["aircraft"] == "no"],
                "VT-CLEAN-001 (reviewed undamaged aircraft)": [r["file"] for r in csv.DictReader(open(ROOT / "datasets/hard_cases/VT-CLEAN-001/manifest.csv", encoding="utf-8")) if r["status"] == "clean"]}
        neg = {}
        for name, files in rows.items():
            neg[name] = {}
            for f in files:
                with Image.open(C.resolve(f)) as im:
                    im.load(); neg[name][f] = [[d["cls"], d["score"], d["xyxy"]] for d in P.raw_detections(im)[0]]
        negc.write_text(json.dumps(neg))
    res = {"bundle": a.bundle, "model_sha256": meta["files"]["model.pt"]["sha256"], "split": "valid", "thresholds": th, "variants": {}}
    variants = {"baseline": None, **{f"contain@{c}": float(c) for c in a.variants.split(",")}}
    runs = {}
    for name, c in variants.items():
        pv = base if c is None else {k: contain_suppress(v, c) for k, v in base.items()}
        runs[name] = pv
        m = E.evaluate_preds(gts, pv, sizes, classes, match_iou=miou, score_threshold=th)
        fa = {}
        for nm, imgs in neg.items():
            n = sum(len([p for p in (v if c is None else contain_suppress([tuple(x) for x in v], c)) if p[1] >= th[classes[p[0]]]]) for v in imgs.values())
            fa[nm] = round(n / max(1, len(imgs)), 3)
        pc = {k: {**v, "f1": round(f1(v["precision"] or 0, v["recall"] or 0), 4)} for k, v in m["per_class_at_threshold"].items()}
        res["variants"][name] = {"AP50": m["AP50"], "AP50_cls": {k: v["AP50"] for k, v in m["per_class_ap"].items()}, "per_class": pc, "false_alarms_per_image": fa}
        print(name, "AP50", round(m["AP50"], 4), {k: (round(v["precision"] or 0, 3), round(v["recall"] or 0, 3), v["f1"]) for k, v in pc.items()}, fa, flush=True)
    # paired image-level bootstrap of the dent F1 difference against the baseline
    ids = list(gts); rng = random.Random(20261029)
    per_img = {name: {k: prf({k: gts[k]}, {k: pv[k]}, classes, th, miou) for k in ids} for name, pv in runs.items()}
    for name in variants:
        if name == "baseline":
            continue
        diffs = []
        for _ in range(1000):
            s = [rng.choice(ids) for _ in ids]
            def f1_of(n):
                tp = sum(per_img[n][k]["dent"][0] for k in s); fp = sum(per_img[n][k]["dent"][1] for k in s); fn = sum(per_img[n][k]["dent"][2] for k in s)
                p = tp / (tp + fp) if tp + fp else 0; r = tp / (tp + fn) if tp + fn else 0
                return f1(p, r)
            diffs.append(f1_of(name) - f1_of("baseline"))
        diffs.sort()
        res["variants"][name]["dent_f1_diff_ci95"] = [round(diffs[25], 4), round(diffs[974], 4)]
        print(name, "dent F1 diff 95% CI", res["variants"][name]["dent_f1_diff_ci95"], flush=True)
    (ROOT / a.out).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
