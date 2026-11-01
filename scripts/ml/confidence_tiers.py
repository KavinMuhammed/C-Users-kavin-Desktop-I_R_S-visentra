"""Measure precision and recall per class as a function of the model score on the VALIDATION split, and derive the
display tiers used by the inspection UI ("likely" / "possible" / "low confidence"). Never reads the test split.

    python scripts/ml/confidence_tiers.py [--bundle models/detection/VT-DET-0002] [--run experiments/...] [--target-precision 0.5]
Writes experiments/confidence_tiers/<model>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
import evaluate_hard_cases as H  # noqa: E402


def match(gts, preds, cls, thr, iou_thr):
    tp = fp = 0; npos = 0
    for k in gts:
        g = [b for c, b in gts[k] if c == cls]; npos += len(g)
        p = sorted([x for x in preds[k] if x[0] == cls and x[1] >= thr], key=lambda x: -x[1])
        used = set()
        for _, s, b in p:
            best, bi = 0, None
            for i, gb in enumerate(g):
                if i in used:
                    continue
                v = H.iou(b, gb)
                if v > best:
                    best, bi = v, i
            if best >= iou_thr:
                tp += 1; used.add(bi)
            else:
                fp += 1
    return tp, fp, npos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002"); ap.add_argument("--run")
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json"); ap.add_argument("--target-precision", type=float, default=0.5)
    a = ap.parse_args()
    P = H.load_predictor(ROOT / a.bundle, ROOT / a.run if a.run else None)
    classes = P.meta["classes"]; miou = P.meta["thresholds"]["match_iou"]
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg)
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    gts, preds = {}, {}
    for it in items:
        with Image.open(C.resolve(it.rel_path)) as im:
            im.load(); W, Hh = ImageOps.exif_transpose(im).size
            preds[it.file_id] = [(d["cls"], d["score"], d["xyxy"]) for d in P.raw_detections(im)[0]]
        gts[it.file_id] = [(b.cls, [b.x1 * W, b.y1 * Hh, b.x2 * W, b.y2 * Hh]) for b in it.boxes]
    grid = [round(0.025 * i, 3) for i in range(1, 39)]
    out = {"model": P.meta.get("model_id"), "split": "valid", "images": len(items), "match_iou": miou, "target_precision": a.target_precision, "classes": {}}
    th = P.meta["thresholds"]
    for ci, c in enumerate(classes):
        curve = []
        for t in grid:
            tp, fp, npos = match(gts, preds, ci, t, miou)
            curve.append({"score": t, "tp": tp, "fp": fp, "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / npos, 3) if npos else None})
        likely = next((x["score"] for x in curve if x["precision"] is not None and x["precision"] >= a.target_precision and x["tp"] >= 5), None)
        rv, sc = th["review_threshold"][c], th["score_threshold"][c]

        def band(lo, hi):
            tp, fp, _ = match(gts, preds, ci, lo, miou)
            if hi is not None:
                tp2, fp2, _ = match(gts, preds, ci, hi, miou); tp, fp = tp - tp2, fp - fp2
            return {"from": lo, "to": hi, "tp": tp, "fp": fp, "precision": round(tp / (tp + fp), 3) if tp + fp else None}
        out["classes"][c] = {"score_threshold": sc, "review_threshold": rv, "likely_threshold": likely, "curve": curve,
                             "bands": {"low": band(sc, rv), "possible": band(rv, likely), "likely": band(likely, None) if likely else None}}
        print(c, "likely>=", likely, out["classes"][c]["bands"])
    o = ROOT / "experiments/confidence_tiers" / f"{(out['model'] or 'model').replace(':', '_').replace('/', '_')}.json"
    o.parent.mkdir(parents=True, exist_ok=True); o.write_text(json.dumps(out, indent=1)); print("wrote", o)


if __name__ == "__main__":
    main()
