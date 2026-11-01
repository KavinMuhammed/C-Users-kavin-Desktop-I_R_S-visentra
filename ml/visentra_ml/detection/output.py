"""Detection output contract (Phase 7 §14) — what a VisionTra detector returns for one image.

Schema ``visiontra.detection_output/1``::

    {
      "schema": "visiontra.detection_output/1",
      "model": {"model_id", "version", "readiness", "supported_classes": [..], "experimental_classes": [..]},
      "input": {"image_ref", "width", "height", "orientation", "coordinate_frame": "oriented_image_pixels",
                "preprocessing": {"mode", "S_w", "S_h", "r_x", "r_y", "pad_x", "pad_y"}},
      "thresholds": {"score_threshold": {class: t}, "nms_iou", "review_threshold": {class: t} | null},
      "inferred_at": ISO-8601 UTC,
      "status": "detections" | "no_detections",
      "detections": [{"ordinal", "class_id", "class_name", "score", "box_xyxy", "box_normalised", "clipped", "low_confidence"}],
      "suppressed": {"below_threshold", "degenerate", "candidates_after_nms", "below_threshold_top": [..]},
      "warnings": [..]   # always includes the no-detection and decision-support statements
    }

``below_threshold_top`` keeps the best few candidates that the per-class thresholds removed (class, score, normalised box)
as engineering diagnostics: they explain a miss ("found, but scored under the threshold" vs "not found at all"). They are
never detections, are never shown as findings and are not part of ``detections``.

``score`` is the model's confidence in its own box. It is not severity and not a probability of an
airworthiness problem. "no_detections" is a valid outcome and never means "defect-free".
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

SCHEMA = "visiontra.detection_output/1"
NO_DETECTION_WARNING = "No detection above the threshold. This does not establish that the surface is free of defects."
SUPPORT_WARNING = "Decision support only: every detection must be reviewed by a qualified person before any maintenance decision."
DIAGNOSTIC_TOP = 5


class InvalidModelOutput(ValueError):
    code = "INVALID_MODEL_OUTPUT"


def build(model_meta: dict, image: dict, prep: dict, dets: list[dict], *, score_threshold: dict, nms_iou: float,
          review_threshold: dict | None = None, now: datetime | None = None) -> dict:
    """dets: [{"cls", "score", "xyxy", "clipped"}] in oriented-image pixels (after NMS)."""
    classes = model_meta["classes"]
    kept, below, below_top = [], 0, []
    W, H = image["width"], image["height"]
    for d in sorted(dets, key=lambda d: -d["score"]):
        name = classes[d["cls"]] if 0 <= d["cls"] < len(classes) else None
        if name is None or not math.isfinite(d["score"]) or not 0 <= d["score"] <= 1 or not all(math.isfinite(v) for v in d["xyxy"]):
            raise InvalidModelOutput(f"invalid detection {d!r}")
        x1, y1, x2, y2 = d["xyxy"]
        if d["score"] < score_threshold[name]:
            below += 1
            if len(below_top) < DIAGNOSTIC_TOP:
                below_top.append({"class_name": name, "score": round(d["score"], 6), "threshold": score_threshold[name],
                                  "box_normalised": {"x": round(x1 / W, 4), "y": round(y1 / H, 4), "w": round((x2 - x1) / W, 4), "h": round((y2 - y1) / H, 4)}})
            continue
        kept.append({"ordinal": len(kept) + 1, "class_id": d["cls"], "class_name": name, "score": round(d["score"], 6),
                     "box_xyxy": [round(v, 2) for v in d["xyxy"]],
                     "box_normalised": {"x": x1 / W, "y": y1 / H, "w": (x2 - x1) / W, "h": (y2 - y1) / H},
                     "clipped": bool(d.get("clipped", False)),
                     "low_confidence": bool(review_threshold and d["score"] < review_threshold[name])})
    out = {
        "schema": SCHEMA,
        "model": {k: model_meta.get(k) for k in ("model_id", "version", "readiness", "supported_classes", "experimental_classes")},
        "input": {"image_ref": image.get("ref"), "width": image["width"], "height": image["height"], "orientation": image.get("orientation", 1),
                  "coordinate_frame": "oriented_image_pixels", "preprocessing": {k: prep[k] for k in ("mode", "S_w", "S_h", "r_x", "r_y", "pad_x", "pad_y")}},
        "thresholds": {"score_threshold": dict(score_threshold), "nms_iou": nms_iou, "review_threshold": dict(review_threshold) if review_threshold else None},
        "inferred_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "detections" if kept else "no_detections",
        "detections": kept,
        "suppressed": {"below_threshold": below, "degenerate": sum(1 for d in dets if d.get("degenerate")), "candidates_after_nms": len(dets),
                       "below_threshold_top": below_top},
        "warnings": ([] if kept else [NO_DETECTION_WARNING]) + [SUPPORT_WARNING],
    }
    validate(out)
    return out


def validate(o: dict) -> None:
    """Raise InvalidModelOutput unless ``o`` satisfies the contract."""
    def bad(msg):
        raise InvalidModelOutput(msg)
    if o.get("schema") != SCHEMA:
        bad("schema")
    W, H = o["input"]["width"], o["input"]["height"]
    if not (isinstance(W, int) and isinstance(H, int) and W > 0 and H > 0):
        bad("image size")
    classes_ok = set(o["model"].get("supported_classes") or []) | set(o["model"].get("experimental_classes") or [])
    if o["status"] not in ("detections", "no_detections") or (o["status"] == "no_detections") != (not o["detections"]):
        bad("status")
    if o["status"] == "no_detections" and NO_DETECTION_WARNING not in o["warnings"]:
        bad("no-detection warning missing")
    if SUPPORT_WARNING not in o["warnings"]:
        bad("decision-support warning missing")
    for i, d in enumerate(o["detections"], 1):
        if d["ordinal"] != i:
            bad("ordinal")
        if d["class_name"] not in classes_ok:
            bad(f"class {d['class_name']} not declared by the model")
        if not (0 <= d["score"] <= 1):
            bad("score outside [0,1]")
        x1, y1, x2, y2 = d["box_xyxy"]
        if not (0 <= x1 < x2 <= W + 0.01 and 0 <= y1 < y2 <= H + 0.01):
            bad(f"box outside the image or degenerate: {d['box_xyxy']}")
        if any(k in d for k in ("severity", "risk", "probability_of_failure")):
            bad("severity-like field present (decision D-07)")
    scores = [d["score"] for d in o["detections"]]
    if scores != sorted(scores, reverse=True):
        bad("detections not ordered by score")
