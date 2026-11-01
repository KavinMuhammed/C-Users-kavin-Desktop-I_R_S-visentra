"""Structured error analysis of a detector bundle on the VALIDATION split (Phase 2, 29 Oct). Never reads the test split.

    python scripts/ml/error_analysis.py [--bundle models/detection/VT-DET-0002] [--out experiments/phase2/error_analysis]

Per class, at the bundle's operating thresholds and the repository's match IoU (0.5):
  · every ground-truth box is one of: tp, low_score (right class, IoU >= 0.5, score under threshold), localization (right
    class above threshold but 0.1 <= IoU < 0.5), class_confusion (another class above threshold, IoU >= 0.5), missed;
  · every prediction above threshold that is not a TP is one of: duplicate, localization, class_confusion,
    background_on_defect_image, background_on_clean_image (no box of that class in the image);
  · TP/FN by ground-truth box size (sqrt of relative area), precision/recall at IoU 0.3 as a looseness diagnostic;
  · label checks (invalid, tiny, near-full-frame and duplicated boxes);
  · a reliability table (score bin -> share of boxes that matched): validation was used to choose the thresholds, so this
    is an in-sample description, not a calibration result;
  · an annotation review queue (CSV + overlays: ground truth green, predictions red) of the highest-impact cases.
Predictions are cached (preds.json) so the analysis can be re-run without inference.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
import evaluate_hard_cases as H  # noqa: E402


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def size_bin(box, W, H_):
    r = math.sqrt(max(1e-9, (box[2] - box[0]) * (box[3] - box[1]) / (W * H_)))
    return "xs <5%" if r < 0.05 else "s 5-12%" if r < 0.12 else "m 12-30%" if r < 0.30 else "l >=30%"


def greedy(gt, pr, thr_iou):
    """Repository-style greedy matching per class: predictions by descending score, each GT used once."""
    used, tp = set(), []
    for k, (_, s, b) in sorted(enumerate(pr), key=lambda x: -x[1][1]):
        best, bi = 0.0, None
        for j, g in enumerate(gt):
            if j in used:
                continue
            v = iou(b, g)
            if v > best:
                best, bi = v, j
        if bi is not None and best >= thr_iou:
            used.add(bi); tp.append(k)
    return used, set(tp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002")
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json", help="only for the validation item list")
    ap.add_argument("--out", default="experiments/phase2/error_analysis")
    ap.add_argument("--queue", type=int, default=60, help="images in the annotation review queue")
    a = ap.parse_args()
    out = ROOT / a.out; out.mkdir(parents=True, exist_ok=True); (out / "overlays").mkdir(exist_ok=True)
    meta = json.loads((ROOT / a.bundle / "metadata.json").read_text())
    classes, th, miou = meta["classes"], meta["thresholds"]["score_threshold"], meta["thresholds"]["match_iou"]
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg)
    assert mp["targets"] == classes
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    cache = out / "preds.json"
    if cache.exists() and json.loads(cache.read_text()).get("bundle_sha256") == meta["files"]["model.pt"]["sha256"]:
        P_ = json.loads(cache.read_text())["preds"]
    else:
        import torch
        Pm = H.load_predictor(ROOT / a.bundle, None)
        if torch.cuda.is_available():
            Pm.model = Pm.model.cuda(); _f = Pm.model.forward
            Pm.model.forward = lambda x, *r, **k: _f(x.cuda(), *r, **k).cpu()
        P_ = {}
        for k, it in enumerate(items):
            with Image.open(C.resolve(it.rel_path)) as im:
                im.load()
                P_[it.file_id] = [[d["cls"], round(d["score"], 6), [round(v, 2) for v in d["xyxy"]]] for d in Pm.raw_detections(im)[0]]
            if (k + 1) % 200 == 0:
                print(f"  predicted {k + 1}/{len(items)}", flush=True)
        cache.write_text(json.dumps({"bundle": a.bundle, "bundle_sha256": meta["files"]["model.pt"]["sha256"], "split": "valid", "preds": P_}))

    res = {"bundle": a.bundle, "model_sha256": meta["files"]["model.pt"]["sha256"], "split": "valid", "images": len(items),
           "match_iou": miou, "thresholds": th, "classes": {}}
    labels = Counter(); rel_items = {}
    fn_rows, fp_rows = [], []
    rel = defaultdict(lambda: [[0, 0] for _ in range(10)])                    # class -> 10 score bins of [boxes, matched]
    for it in items:
        with Image.open(C.resolve(it.rel_path)) as im:
            W, H_ = ImageOps.exif_transpose(im).size
        rel_items[it.file_id] = (it.rel_path, W, H_)
        gts = [(classes[b.cls], [b.x1 * W, b.y1 * H_, b.x2 * W, b.y2 * H_]) for b in it.boxes]
        for c, b in gts:                                                      # label integrity
            if not (b[2] > b[0] and b[3] > b[1]): labels[f"{c}: invalid"] += 1
            ra = (b[2] - b[0]) * (b[3] - b[1]) / (W * H_)
            if ra < 0.0005: labels[f"{c}: tiny (<0.05% of image)"] += 1
            if ra > 0.8: labels[f"{c}: near full frame (>80%)"] += 1
        for i in range(len(gts)):
            for j in range(i + 1, len(gts)):
                if gts[i][0] == gts[j][0] and iou(gts[i][1], gts[j][1]) > 0.7: labels[f"{gts[i][0]}: duplicated box (IoU>0.7)"] += 1
                elif gts[i][0] != gts[j][0] and iou(gts[i][1], gts[j][1]) > 0.5: labels[f"{gts[i][0]}/{gts[j][0]}: same region, two classes"] += 1
        preds = [(classes[c], s, b) for c, s, b in P_.get(it.file_id, [])]
        for c in classes:
            g = [b for k, b in gts if k == c]
            pa = [p for p in preds if p[0] == c and p[1] >= th[c]]
            used, tpk = greedy(g, pa, miou)
            used30, _ = greedy(g, pa, 0.3)
            C_ = res["classes"].setdefault(c, {"gt": 0, "fn": Counter(), "fp": Counter(), "tp": 0, "tp_iou30": 0, "by_size": defaultdict(Counter)})
            C_["gt"] += len(g); C_["tp"] += len(used); C_["tp_iou30"] += len(used30)
            for j, gb in enumerate(g):
                sb = size_bin(gb, W, H_)
                if j in used:
                    C_["by_size"][sb]["tp"] += 1; continue
                C_["by_size"][sb]["fn"] += 1
                low = max((p[1] for p in preds if p[0] == c and iou(p[2], gb) >= miou), default=0.0)
                loc = any(0.1 <= iou(p[2], gb) < miou for p in pa)
                conf = [p for p in preds if p[0] != c and p[1] >= th[p[0]] and iou(p[2], gb) >= miou]
                why = "localization" if loc else "class_confusion" if conf else "low_score" if low > 0 else "missed"
                C_["fn"][why] += 1
                fn_rows.append({"file_id": it.file_id, "class": c, "reason": why, "best_same_class_score": round(low, 4), "size": sb, "box": [round(v) for v in gb]})
            for k, p in enumerate(pa):
                if k in tpk:
                    continue
                if any(iou(p[2], gb) >= miou for gb in g): why = "duplicate"
                elif any(0.1 <= iou(p[2], gb) < miou for gb in g): why = "localization"
                elif any(iou(p[2], b) >= 0.3 for kk, b in gts if kk != c): why = "class_confusion"
                elif g: why = "background_on_defect_image"
                else: why = "background_on_clean_image"
                C_["fp"][why] += 1
                fp_rows.append({"file_id": it.file_id, "class": c, "reason": why, "score": p[1], "box": [round(v) for v in p[2]]})
            # reliability: all predictions of this class (floor 0.001), matched at IoU 0.5 greedily
            allp = [p for p in preds if p[0] == c]
            _, tpa = greedy(g, allp, miou)
            for k, p in enumerate(allp):
                bi = min(9, int(p[1] * 10)); rel[c][bi][0] += 1; rel[c][bi][1] += int(k in tpa)
    for c, C_ in res["classes"].items():
        fp = sum(C_["fp"].values())
        C_["precision"] = round(C_["tp"] / (C_["tp"] + fp), 4) if C_["tp"] + fp else None
        C_["recall"] = round(C_["tp"] / C_["gt"], 4) if C_["gt"] else None
        C_["recall_iou30"] = round(C_["tp_iou30"] / C_["gt"], 4) if C_["gt"] else None
        C_["fn"], C_["fp"] = dict(C_["fn"]), dict(C_["fp"])
        C_["by_size"] = {k: dict(v) for k, v in sorted(C_["by_size"].items())}
        C_["reliability"] = [{"bin": f"{k / 10:.1f}-{(k + 1) / 10:.1f}", "boxes": n, "matched": m, "share": round(m / n, 3) if n else None} for k, (n, m) in enumerate(rel[c]) if n]
    res["label_checks"] = dict(labels)
    res["note"] = ("Validation split: also used to choose thresholds, so these are development diagnostics. Labels are the public "
                   "source annotations under provisional class mappings, not verified by a qualified inspector.")
    (out / "summary.json").write_text(json.dumps(res, indent=1))
    # review queue: dent first — FNs that the model saw with the right class (low score / localization), then high-score FPs
    impact = sorted([r for r in fn_rows if r["class"] == "dent" and r["reason"] in ("low_score", "localization", "class_confusion")], key=lambda r: -r["best_same_class_score"])
    fps = sorted([r for r in fp_rows if r["class"] == "dent"], key=lambda r: -r["score"])
    fns = impact
    seen, queue = set(), []
    for pool, cap in ((fns, a.queue // 2), (fps, a.queue)):              # half the queue from each error type
        for r in pool:
            if r["file_id"] in seen or len(queue) >= cap:
                continue
            seen.add(r["file_id"]); queue.append(r)
    with open(out / "annotation_review_queue.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file_id", "image", "class", "error", "score_or_best_score", "box_xyxy", "overlay", "label_status", "adjudication", "adjudicator", "adjudicated_at"])
        for r in queue:
            relp, W, H_ = rel_items[r["file_id"]]
            ov = out / "overlays" / (re.sub(r"[^A-Za-z0-9_.-]+", "_", r["file_id"])[-120:] + ".jpg")
            with Image.open(C.resolve(relp)) as im:
                im = ImageOps.exif_transpose(im).convert("RGB"); d = ImageDraw.Draw(im); lw = max(2, W // 300)
                it = next(i for i in items if i.file_id == r["file_id"])
                for b in it.boxes:
                    d.rectangle([b.x1 * W, b.y1 * H_, b.x2 * W, b.y2 * H_], outline=(40, 220, 90), width=lw); d.text((b.x1 * W + 3, b.y1 * H_ + 3), "GT " + classes[b.cls], fill=(40, 220, 90))
                for c, s, b in P_.get(r["file_id"], []):
                    if s >= th[classes[c]]:
                        d.rectangle(b, outline=(240, 60, 60), width=lw); d.text((b[0] + 3, b[3] - 12), f"{classes[c]} {s:.2f}", fill=(240, 60, 60))
                im.thumbnail((1024, 1024)); im.save(ov, quality=85)
            w.writerow([r["file_id"], relp, r["class"], r["reason"], r.get("score", r.get("best_same_class_score")), r["box"], ov.relative_to(ROOT).as_posix(),
                        "UNVERIFIED (source label, no qualified review)", "", "", ""])
    print(json.dumps({c: {k: v for k, v in C_.items() if k in ("gt", "tp", "precision", "recall", "recall_iou30", "fn", "fp")} for c, C_ in res["classes"].items()}, indent=1))
    print("labels:", dict(labels)); print("queue:", len(queue))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
