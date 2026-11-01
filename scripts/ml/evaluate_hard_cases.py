"""Nose-damage hard-case evaluation: per image, did the model localise each labelled defect, and what else did it flag?

    python scripts/ml/evaluate_hard_cases.py --bundle models/detection/VT-DET-0002 [--run experiments/VT-EXP-009/<run>]
        [--probes probes.json] [--out experiments/hard_cases/<name>.json]

Evaluates VT-HC-001 images with split 'eval' (never trained) and, optionally, local incident probes (images the user
uploaded; not redistributable, not trainable) given as a JSON list of {file, boxes:[{cls, xyxy}]}.

For every ground-truth box: the best same-class prediction by IoU (>= 0.3, because large irregular damage has no
crisp extent) and its score; 'detected' = such a prediction at or above the model's per-class score threshold.
Predictions at or above threshold that overlap no ground truth (IoU < 0.1, any class) are counted as unmatched
detections (possible false alarms; the images are not exhaustively labelled, so they are reported, not scored).
The collection is small (single-digit images); results are reported per image, not as AP.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import evaluation as V  # noqa: E402
from visentra_ml.detection.inference import Predictor  # noqa: E402


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def load_predictor(bundle: Path, run: Path | None):
    P = Predictor(bundle, threads=None)
    if run is not None:                                   # same pre/post-processing as the bundle, run's weights
        m, cfg, rec, ck = V.load_run_model(run, "best")
        P.model = m
        P.meta = {**P.meta, "input_size": cfg["model"]["input_size"], "model_id": f"{run.name}:best", "checkpoint": ck}
        tf = run / "thresholds.json"                      # the candidate's own validation-selected thresholds
        if tf.exists():
            t = json.loads(tf.read_text())["classes"]
            P.meta["thresholds"] = {**P.meta["thresholds"], "score_threshold": {c: t[c]["score_threshold"] for c in t},
                                    "review_threshold": {c: t[c]["review_threshold"] for c in t}}
    return P


def cases(probes: Path | None):
    ann = json.loads((ROOT / "datasets/hard_cases/VT-HC-001/annotations.json").read_text())
    out = []
    for rel, v in sorted(ann["images"].items()):
        if v["status"] == "labelled" and v["split"] == "eval":
            out.append({"file": str(ROOT / rel), "id": Path(rel).name, "source": "VT-HC-001", "framing": v["framing"],
                         "boxes": [{"cls": b["cls"], "xyxy": b["xyxy"], "quality": b["label_quality"]} for b in v["boxes"]]})
    if probes:
        for p in json.loads(Path(probes).read_text()):
            out.append({**p, "id": Path(p["file"]).name, "source": "incident probe (local only)"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002")
    ap.add_argument("--run")
    ap.add_argument("--probes")
    ap.add_argument("--out")
    ap.add_argument("--tiles", type=int, default=1, help="1 = full image (default); N = full image + NxN overlapping tiles (evaluate_tiled.predict_tiled)")
    a = ap.parse_args()
    P = load_predictor(ROOT / a.bundle, ROOT / a.run if a.run else None)
    classes = P.meta["classes"]; th = P.meta["thresholds"]["score_threshold"]
    rows, n_gt, n_hit, n_ctr = [], 0, 0, 0
    for c in cases(a.probes):
        with Image.open(c["file"]) as im:
            im.load()
            if a.tiles > 1:
                import evaluate_tiled as T
                preds = [(classes[c], s_, b) for c, s_, b in T.predict_tiled(P, im, a.tiles, 0.2, P.meta["thresholds"]["nms_iou"])]
            else:
                dets, _, _ = P.raw_detections(im)
                preds = [(classes[d["cls"]], d["score"], d["xyxy"]) for d in dets]
        gts = []
        for g in c["boxes"]:
            same = [(iou(g["xyxy"], p[2]), p[1]) for p in preds if p[0] == g["cls"]]
            best = max((s for i, s in same if i >= 0.3), default=0.0)
            anycls = max((p[1] for p in preds if iou(g["xyxy"], p[2]) >= 0.3), default=0.0)
            hit = best >= th[g["cls"]]
            # thin diagonal cracks and irregular damage have boxes that are mostly background, so IoU undersells a
            # correct localisation: also count a same-class prediction whose centre lies inside the ground-truth box
            x1, y1, x2, y2 = g["xyxy"]
            ctr = max((p[1] for p in preds if p[0] == g["cls"] and x1 <= (p[2][0] + p[2][2]) / 2 <= x2 and y1 <= (p[2][1] + p[2][3]) / 2 <= y2), default=0.0)
            n_ctr += int(ctr >= th[g["cls"]])
            n_gt += 1; n_hit += int(hit)
            gts.append({**g, "best_same_class_score": round(best, 4), "best_any_class_score": round(anycls, 4), "threshold": th[g["cls"]], "detected": hit,
                        "best_centre_inside_score": round(ctr, 4), "detected_centre_inside": ctr >= th[g["cls"]]})
        unmatched = [{"cls": p[0], "score": round(p[1], 4), "xyxy": [round(v) for v in p[2]]} for p in preds
                     if p[1] >= th[p[0]] and all(iou(p[2], g["xyxy"]) < 0.1 for g in c["boxes"])]
        rows.append({"id": c["id"], "source": c["source"], "framing": c.get("framing"), "gt": gts, "unmatched_above_threshold": unmatched[:20],
                     "n_unmatched": len(unmatched)})
        print(f"{c['id'][:48]:48s} " + " ".join(f"{g['cls']}:{g['best_same_class_score']:.3f}{'*' if g['detected'] else ''}/c{g['best_centre_inside_score']:.3f}{'*' if g['detected_centre_inside'] else ''}" for g in gts) + f"  unmatched={len(unmatched)}", flush=True)
    res = {"schema": "visiontra.hard_case_evaluation/1", "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "model": P.meta.get("model_id"), "bundle": a.bundle, "run": a.run, "tiles": a.tiles, "thresholds": th, "iou_match": 0.3,
           "ground_truth_boxes": n_gt, "detected": n_hit, "detected_centre_inside": n_ctr, "images": rows}
    out = Path(a.out) if a.out else ROOT / "experiments/hard_cases" / f"{(a.run or a.bundle).replace('/', '_')}.json"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(res, indent=1))
    print(f"detected {n_hit}/{n_gt} (IoU>=0.3), {n_ctr}/{n_gt} (centre inside) ground-truth boxes; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
