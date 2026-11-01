"""Evaluation of a trained detector: validation-based threshold selection, final held-out test
evaluation, error analysis, robustness probes and diagnostic images.

Protocol (MLE-01…08, SPL-02):
- Thresholds are chosen on the **validation** split only and frozen in ``thresholds.json``.
- The test split is evaluated once per run (``--final``); a second test evaluation of the same run
  is refused unless a reason is given, and every test evaluation is recorded.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from . import config as C
from . import data as Dt
from . import engine as E
from . import metrics as M
from . import model as Mo

SIZE_BUCKETS = [(0, 0.05, "tiny (<5% of side)"), (0.05, 0.10, "small (5–10%)"), (0.10, 0.20, "medium (10–20%)"), (0.20, 10, "large (≥20%)")]


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_run_model(run_dir: Path, which: str = "best"):
    cfg = json.loads((run_dir / "config.json").read_text())
    rec = json.loads((run_dir / "run.json").read_text())
    ck_path = run_dir / "checkpoints" / f"{which}.pt"
    blob = torch.load(ck_path, map_location="cpu", weights_only=True)
    sd = blob["state_dict"] if "state_dict" in blob else (blob["ema"] or blob["model"])
    m = Mo.build(cfg["model"]["variant"], len(rec["classes"]))
    m.load_state_dict(sd, strict=True); m.eval()
    return m, cfg, rec, {"checkpoint": f"checkpoints/{which}.pt", "sha256": Mo.sha256_file(ck_path), "epoch": blob.get("epoch")}


def fbeta_curve(gts, preds, cls_index, iou_thr, grid=None):
    grid = grid or [round(0.025 * i, 3) for i in range(1, 39)]
    rows = M.best_f1_threshold(gts, preds, cls_index, iou_thr=iou_thr, grid=grid)["curve"]
    for r in rows:
        p, rc = r["precision"], r["recall"]
        r["f2"] = None if rc is None else (0.0 if p is None or (4 * p + rc) == 0 else 5 * p * rc / (4 * p + rc))
    return rows


def select_thresholds(gts, preds, classes, iou_thr) -> dict:
    """Validation-only operating points per class.

    review_threshold = max-F1 threshold (balanced). score_threshold = max-F2 threshold (recall-weighted,
    because a missed defect is the costly error), capped at the review threshold. Detections between the
    two are shown as low-confidence. Returns the full curves so the trade-off is documented (MLE-07).
    """
    out = {"policy": "score_threshold = argmax F2 (recall-weighted), review_threshold = argmax F1; chosen on the validation split", "classes": {}}
    for ci, name in enumerate(classes):
        rows = fbeta_curve(gts, preds, ci, iou_thr)
        f1 = [r for r in rows if r["f1"] is not None]; f2 = [r for r in rows if r["f2"] is not None]
        if not f1:
            out["classes"][name] = {"score_threshold": None, "review_threshold": None, "note": "no validation ground truth — thresholds undefined", "curve": rows}
            continue
        b1 = max(f1, key=lambda r: (r["f1"], -r["threshold"])); b2 = max(f2, key=lambda r: (r["f2"], -r["threshold"]))
        st = min(b2["threshold"], b1["threshold"])
        out["classes"][name] = {"score_threshold": st, "review_threshold": b1["threshold"],
                                "at_score_threshold": next(r for r in rows if r["threshold"] == st), "at_review_threshold": b1, "curve": rows}
    return out


def size_bucket(box, w, h):
    s = math.sqrt(max(0.0, (box[2] - box[0]) * (box[3] - box[1]))) / max(w, h)
    for lo, hi, name in SIZE_BUCKETS:
        if lo <= s < hi:
            return name
    return SIZE_BUCKETS[-1][2]


def error_analysis(gts, preds, sizes, classes, thr: dict, iou_thr: float) -> dict:
    rows, by_size = [], {n: {"gt": 0, "tp": 0} for _, _, n in SIZE_BUCKETS}
    fp_on_no_gt, imgs_no_gt = 0, 0
    for img in sorted(sizes):
        W, H = sizes[img]
        g = gts.get(img, []); p = [x for x in preds.get(img, []) if x[1] >= thr[classes[x[0]]]]
        m = M.match({img: g}, {img: p}, iou_thr=iou_thr)
        for t in m["tp"]:
            by_size[size_bucket(t["gt"], W, H)]["tp"] += 1
        for gc, gb in g:
            by_size[size_bucket(gb, W, H)]["gt"] += 1
        if not g:
            imgs_no_gt += 1; fp_on_no_gt += len(m["fp"])
        for f in m["fp"]:
            kind = "localisation (IoU 0.1–0.5 with a labelled dent)" if f["best_iou"] >= 0.1 else ("on image without labelled dent" if not g else "no overlap with a labelled dent")
            rows.append({"type": "FP", "image": img, "class": classes[f["cls"]], "score": round(f["score"], 4), "best_iou": round(f["best_iou"], 3),
                         "kind": kind, "size_bucket": size_bucket(f["box"], W, H), "box": [round(v, 1) for v in f["box"]]})
        for f in m["fn"]:
            best = max([M.iou(f["gt"], b) for _, s, b in preds.get(img, [])] or [0.0])
            best_any = max([s for _, s, b in preds.get(img, []) if M.iou(f["gt"], b) >= iou_thr] or [None], key=lambda v: -1 if v is None else v)
            rows.append({"type": "FN", "image": img, "class": classes[f["cls"]], "score": None, "best_iou": round(best, 3),
                         "kind": "detected only below threshold" if best_any is not None else ("poorly localised" if best >= 0.1 else "not detected at all"),
                         "size_bucket": size_bucket(f["gt"], W, H), "box": [round(v, 1) for v in f["gt"]], "best_score_below_threshold": best_any})
    recall_by_size = {k: {**v, "recall": (v["tp"] / v["gt"]) if v["gt"] else None} for k, v in by_size.items()}
    return {"rows": rows, "fp_kinds": dict(Counter(r["kind"] for r in rows if r["type"] == "FP")), "fn_kinds": dict(Counter(r["kind"] for r in rows if r["type"] == "FN")),
            "recall_by_size": recall_by_size, "images_without_labelled_target": imgs_no_gt,
            "fp_per_image_without_labelled_target": (fp_on_no_gt / imgs_no_gt) if imgs_no_gt else None}


def draw_diagnostics(items_by_id, gts, preds, thr, classes, rows, out_dir: Path, *, n: int = 12):
    """Ground truth green, predictions ≥ threshold red (score label). For images with the most errors."""
    from PIL import Image, ImageDraw, ImageOps

    out_dir.mkdir(parents=True, exist_ok=True)
    err = Counter(r["image"] for r in rows)
    written = []
    for img, k in err.most_common(n):
        it = items_by_id[img]
        with Image.open(C.resolve(it.rel_path)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
        d = ImageDraw.Draw(im)
        for _, b in gts.get(img, []):
            d.rectangle(b, outline=(40, 220, 90), width=3)
        for c, s, b in preds.get(img, []):
            if s >= thr[classes[c]]:
                d.rectangle(b, outline=(240, 50, 60), width=2); d.text((b[0] + 3, b[1] + 2), f"{classes[c]} {s:.2f}", fill=(240, 50, 60))
        d.text((6, 6), "GT green · prediction red · VisionTra evaluation diagnostic", fill=(255, 255, 0))
        name = hashlib.sha1(img.encode()).hexdigest()[:12] + ".jpg"
        im.save(out_dir / name, quality=85); written.append({"file": name, "image": img, "errors": k})
    (out_dir / "index.json").write_text(json.dumps(written, indent=1))
    return written


def perturb_items(items, kind: str):
    """Robustness probes on real images: brightness ±, contrast −, half resolution. Returns a dataset subclass factory."""
    class P(Dt.DetectionDataset):
        def load(self, i):
            from PIL import Image, ImageEnhance
            arr, b = super().load(i)
            im = Image.fromarray(arr[:, :, ::-1])
            if kind == "darker_40pct":
                im = ImageEnhance.Brightness(im).enhance(0.6)
            elif kind == "brighter_40pct":
                im = ImageEnhance.Brightness(im).enhance(1.4)
            elif kind == "low_contrast_50pct":
                im = ImageEnhance.Contrast(im).enhance(0.5)
            elif kind == "half_resolution":
                w, h = im.size; im = im.resize((w // 2, h // 2), Image.BILINEAR).resize((w, h), Image.BILINEAR)
            return np.asarray(im)[:, :, ::-1].copy(), b
    return P


def predict_with(dataset_cls, model, items, size, ev, num_classes):
    orig = Dt.DetectionDataset
    try:
        Dt.DetectionDataset = dataset_cls
        return E.predict_items(model, items, size, ev, num_classes=num_classes)
    finally:
        Dt.DetectionDataset = orig


def session_index(cfg) -> dict:
    """file_id → capture session from the Phase 4 derived manifest ('' when unknown, e.g. web photos)."""
    p = C.resolve(f"datasets/processed/{cfg['dataset']['prep_id']}/derived_manifest.csv")
    if not p.is_file():
        return {}
    return {r["file_id"]: r.get("session") or "" for r in csv.DictReader(p.open(encoding="utf-8"))}


def breakdown_by_session(gts, preds, sizes, classes, cfg, *, match_iou, score_threshold) -> dict:
    """Metrics per capture session (phone sessions vs images with no session, mostly web photos)."""
    ses = session_index(cfg)
    groups = {}
    for k in sizes:
        groups.setdefault(ses.get(k) or "no session (web / unknown)", []).append(k)
    out = {}
    for name, keys in sorted(groups.items()):
        g = {k: gts[k] for k in keys}; p = {k: preds[k] for k in keys}; s = {k: sizes[k] for k in keys}
        m = E.evaluate_preds(g, p, s, classes, match_iou=match_iou, score_threshold=score_threshold)
        out[name] = {"images": len(keys), "AP50": m["AP50"], "AP50_95": m["AP50_95"], "at_threshold": m["per_class_at_threshold"]}
    return out


def write_errors_csv(rows, path: Path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["type", "image", "class", "score", "best_iou", "kind", "size_bucket", "box", "best_score_below_threshold"])
        w.writeheader()
        for r in rows:
            w.writerow({**{k: r.get(k) for k in w.fieldnames}, "box": json.dumps(r["box"])})
