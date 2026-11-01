"""Detection metrics.

- ``match``: greedy one-to-one matching per image and class at an IoU threshold, highest score first
  (the COCO/VOC rule). Gives TP / FP / FN counts and the matched pairs for error analysis.
- ``counts_at``: precision, recall and F1 at a score threshold, per class.
- ``coco_ap``: AP50 and AP50–95 from pycocotools (the reference implementation), per class.
- ``best_f1_threshold``: operating-point helper used on the **validation** split only.

Undefined values stay ``None`` (e.g. precision with zero predictions, AP with zero ground truth);
they are never turned into 0 or 1.
"""
from __future__ import annotations

import contextlib
import io
import math
from collections import defaultdict


def iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def validate_box(b, w: float | None = None, h: float | None = None) -> bool:
    """A box is valid when all four values are finite, x1<x2, y1<y2 and (if given) inside [0,w]×[0,h]."""
    if len(b) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in b):
        return False
    if not (b[0] < b[2] and b[1] < b[3]):
        return False
    if w is not None and (b[0] < 0 or b[2] > w):
        return False
    if h is not None and (b[1] < 0 or b[3] > h):
        return False
    return True


def match(gts: dict, preds: dict, *, iou_thr: float = 0.5, score_thr: float = 0.0) -> dict:
    """gts: {image_id: [(cls, [x1,y1,x2,y2]), …]}; preds: {image_id: [(cls, score, [x1,y1,x2,y2]), …]}.

    Returns per-class tp/fp/fn and lists of matched, false-positive and missed items.
    """
    res = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    tps, fps, fns = [], [], []
    for img in sorted(set(gts) | set(preds)):
        g = gts.get(img, []); p = [x for x in preds.get(img, []) if x[1] >= score_thr]
        for c in sorted({x[0] for x in g} | {x[0] for x in p}):
            gc = [(i, b) for i, (cc, b) in enumerate(g) if cc == c]
            pc = sorted([(s, b) for cc, s, b in p if cc == c], key=lambda t: -t[0])
            used = set()
            for s, b in pc:
                best, bi = 0.0, None
                for i, gb in gc:
                    if i in used:
                        continue
                    v = iou(b, gb)
                    if v > best:
                        best, bi = v, i
                if bi is not None and best >= iou_thr:
                    used.add(bi); res[c]["tp"] += 1; tps.append({"image": img, "cls": c, "score": s, "box": b, "gt": g[bi][1], "iou": best})
                else:
                    res[c]["fp"] += 1; fps.append({"image": img, "cls": c, "score": s, "box": b, "best_iou": best})
            for i, gb in gc:
                if i not in used:
                    res[c]["fn"] += 1; fns.append({"image": img, "cls": c, "gt": gb})
    return {"per_class": {c: dict(v) for c, v in res.items()}, "tp": tps, "fp": fps, "fn": fns}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    # F1 is undefined without ground truth; with ground truth but no (correct) predictions it is 0.
    f1 = None if r is None else (0.0 if p is None or p + r == 0 else 2 * p * r / (p + r))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def counts_at(gts, preds, classes: list[str], *, iou_thr: float, score_thr) -> dict:
    """score_thr: a number, or {class_name: number} for per-class thresholds."""
    out = {}
    for ci, name in enumerate(classes):
        thr = score_thr[name] if isinstance(score_thr, dict) else score_thr
        g = {k: [x for x in v if x[0] == ci] for k, v in gts.items()}
        p = {k: [x for x in v if x[0] == ci] for k, v in preds.items()}
        m = match(g, p, iou_thr=iou_thr, score_thr=thr)["per_class"].get(ci, {"tp": 0, "fp": 0, "fn": 0})
        out[name] = {**prf(m["tp"], m["fp"], m["fn"]), "score_threshold": thr, "gt_instances": sum(len(v) for v in g.values())}
    return out


def best_f1_threshold(gts, preds, cls_index: int, *, iou_thr: float, grid=None) -> dict:
    """Score threshold with the highest F1 for one class. Use on VALIDATION data only (MLE-07)."""
    grid = grid or [round(0.05 * i, 2) for i in range(1, 20)]
    g = {k: [x for x in v if x[0] == cls_index] for k, v in gts.items()}
    p = {k: [x for x in v if x[0] == cls_index] for k, v in preds.items()}
    rows = []
    for t in grid:
        m = match(g, p, iou_thr=iou_thr, score_thr=t)["per_class"].get(cls_index, {"tp": 0, "fp": 0, "fn": 0})
        rows.append({"threshold": t, **prf(m["tp"], m["fp"], m["fn"])})
    valid = [r for r in rows if r["f1"] is not None]
    best = max(valid, key=lambda r: (r["f1"], -r["threshold"])) if valid else None
    return {"best": best, "curve": rows}


def coco_ap(gts, preds, classes: list[str], sizes: dict) -> dict:
    """AP via pycocotools. gts/preds as in ``match``; sizes: {image_id: (w, h)}.
    Per class: AP50, AP50_95, gt instances. A class with no ground truth gets None (undefined)."""
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    import numpy as np

    ids = {img: k + 1 for k, img in enumerate(sorted(sizes))}
    ann, det, aid = [], [], 1
    for img, lst in gts.items():
        for c, b in lst:
            ann.append({"id": aid, "image_id": ids[img], "category_id": c + 1, "bbox": [b[0], b[1], b[2] - b[0], b[3] - b[1]],
                        "area": (b[2] - b[0]) * (b[3] - b[1]), "iscrowd": 0}); aid += 1
    for img, lst in preds.items():
        for c, s, b in lst:
            det.append({"image_id": ids[img], "category_id": c + 1, "bbox": [b[0], b[1], b[2] - b[0], b[3] - b[1]], "score": float(s)})
    gt = COCO()
    gt.dataset = {"images": [{"id": i, "width": sizes[k][0], "height": sizes[k][1]} for k, i in ids.items()],
                  "annotations": ann, "categories": [{"id": i + 1, "name": n} for i, n in enumerate(classes)]}
    with contextlib.redirect_stdout(io.StringIO()):
        gt.createIndex()
    out = {}
    for ci, name in enumerate(classes):
        n = sum(1 for a in ann if a["category_id"] == ci + 1)
        if n == 0:
            out[name] = {"AP50": None, "AP50_95": None, "gt_instances": 0, "note": "no ground truth — undefined"}
            continue
        dets = [d for d in det if d["category_id"] == ci + 1]
        if not dets:
            out[name] = {"AP50": 0.0, "AP50_95": 0.0, "gt_instances": n, "note": "no predictions"}
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            dt = gt.loadRes(dets)
            e = COCOeval(gt, dt, "bbox"); e.params.catIds = [ci + 1]; e.params.maxDets = [1, 10, 100]
            e.evaluate(); e.accumulate(); e.summarize()
        prec = e.eval["precision"]  # [T, R, K, A, M]
        def ap_at(t_idx):
            p = prec[t_idx, :, 0, 0, -1]; p = p[p > -1]
            return float(np.mean(p)) if p.size else None
        out[name] = {"AP50": ap_at(0), "AP50_95": float(e.stats[0]), "gt_instances": n,
                     "AP_small": float(e.stats[3]) if e.stats[3] >= 0 else None, "AP_medium": float(e.stats[4]) if e.stats[4] >= 0 else None,
                     "AP_large": float(e.stats[5]) if e.stats[5] >= 0 else None}
    return out
