"""Detection confusion matrix at the bundle's operating thresholds (validation split only).

    python scripts/ml/confusion_matrix.py --bundle models/detection/VT-DET-0002 --out docs/evidence/phaseB/confusion_valid.json

Each labelled box is matched to the highest-IoU retained prediction (any class) with IoU >= match_iou, greedily by score
and one-to-one. Rows: true class. Columns: predicted class, or "missed" when no retained prediction overlaps it.
Retained predictions that match no labelled box are counted in the "background" row (false alarms). Uses the same
Predictor (preprocessing, NMS, coordinate restoration) as the API. The held-out test split is not read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
from visentra_ml.detection.inference import Predictor  # noqa: E402


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002")
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    P = Predictor(ROOT / a.bundle, threads=None)
    if torch.cuda.is_available():
        P.model = P.model.cuda(); _f = P.model.forward
        P.model.forward = lambda x, *r, **k: _f(x.cuda(), *r, **k).cpu()
    classes, th, miou = P.meta["classes"], P.meta["thresholds"]["score_threshold"], P.meta["thresholds"]["match_iou"]
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg)
    assert mp["targets"] == classes
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    rows = classes + ["background"]; cols = classes + ["missed"]
    m = {r: {c: 0 for c in cols} for r in rows}
    for it in items:
        with Image.open(C.resolve(it.rel_path)) as im:
            im.load(); W, H = ImageOps.exif_transpose(im).size
            preds = [(d["cls"], d["score"], d["xyxy"]) for d in P.raw_detections(im)[0] if d["score"] >= th[classes[d["cls"]]]]
        gts = [(b.cls, [b.x1 * W, b.y1 * H, b.x2 * W, b.y2 * H]) for b in it.boxes]
        used = set()
        pairs = sorted(((iou(g[1], p[2]), gi, pi) for gi, g in enumerate(gts) for pi, p in enumerate(preds)), reverse=True)
        gmatch = {}
        for v, gi, pi in pairs:
            if v < miou:
                break
            if gi in gmatch or pi in used:
                continue
            gmatch[gi] = pi; used.add(pi)
        for gi, g in enumerate(gts):
            m[classes[g[0]]][classes[preds[gmatch[gi]][0]] if gi in gmatch else "missed"] += 1
        for pi, p in enumerate(preds):
            if pi not in used:
                m["background"][classes[p[0]]] += 1
    out = {"bundle": a.bundle, "split": "valid", "images": len(items), "subset": cfg["dataset"]["valid_subset"], "thresholds": th, "match_iou": miou,
           "rows_true": rows, "cols_predicted": cols, "matrix": m,
           "per_class": {c: {"gt": sum(m[c].values()), "correct": m[c][c], "confused": {k: v for k, v in m[c].items() if k not in (c, "missed") and v},
                             "missed": m[c]["missed"], "false_alarms_as": m["background"][c]} for c in classes}}
    Path(ROOT / a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ROOT / a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["per_class"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
