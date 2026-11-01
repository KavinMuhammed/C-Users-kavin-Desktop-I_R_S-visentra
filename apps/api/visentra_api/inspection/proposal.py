"""Per-finding proposal: what the record shows, what the model predicts, what is unknown, what evidence comes next, and who decides.

Built on read from data already stored for the inspection (predictions, review decisions, media metadata, inspection fields, the
model bundle's recorded thresholds and validation tiers). It never writes, never re-runs the model and never invents a value the
record does not hold. The three review stages stay separate:

  1. AI proposed finding         — the immutable prediction (class, score, box)
  2. Inspector classification    — the reviewer's latest decision, with name and time; the AI class is kept beside it
  3. Engineering disposition     — always "not established" here: VisionTra holds no aircraft-specific approved data

The score of the current detector (YOLOX) is objectness × the highest class sigmoid for the box. Only the winning class and its
product score are kept (``ml/visentra_ml/detection/model.py``), so no per-box distribution over classes exists and none is shown.
Overlapping boxes of other classes are real separate proposals (class-wise NMS) and are reported as competing proposals.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import settings
from . import guidance as G

NOT_ESTABLISHED = "Not established from the supplied evidence"
SCORE_SEMANTICS = ("Model score = objectness × class confidence for this box (two sigmoid outputs multiplied). It ranks the model's own "
                   "boxes; it is not a calibrated probability that a defect is present, and it is not size, severity or airworthiness risk.")
TIER_SEMANTICS = ("Validation hit rate: the share of the model's boxes in this class and score band that matched a labelled defect on the "
                  "validation split (IoU ≥ 0.5). The same split was used to choose the thresholds, the labels were not verified by a "
                  "qualified inspector, and photos come from a small number of aircraft, so it is an in-sample indication, not a "
                  "calibrated probability for this image.")
CLASS_LABEL = {"dent": "dent", "crack": "crack or skin rupture", "corrosion": "corrosion", "other": "other"}
_OVERLAP = 0.3          # IoU above which two boxes are reported as covering the same area (descriptive only; not a decision rule)


def _bundle_json(model_id: str | None, name: str) -> dict | None:
    if not model_id:
        return None
    try:
        return json.loads((Path(settings.detection_models_dir) / model_id / name).read_text())
    except (OSError, ValueError):
        return None


def _iou(a: dict, b: dict) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2, y2 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _where(box: dict) -> str:
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    v = "upper" if cy < 1 / 3 else "lower" if cy > 2 / 3 else "middle"
    h = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "centre"
    return "centre of the image" if (v, h) == ("middle", "centre") else f"{v} {h} of the image"


def _tier(tiers: dict | None, cls: str, score: float) -> dict | None:
    bands = (((tiers or {}).get("classes") or {}).get(cls) or {}).get("bands") or []
    for b in bands:
        if score >= b["from"] - 1e-9 and (b.get("to") is None or score < b["to"] - 1e-9):
            return {"tier": b["tier"], "validation_hit_rate": b.get("hit_rate"), "validation_boxes": b.get("boxes"),
                    "band": [b["from"], b.get("to")], "split": (tiers or {}).get("split"), "semantics": TIER_SEMANTICS}
    return None


def _review(kind: str, cur: dict, ai_class: str | None, finding: dict) -> dict:
    """Stage 2. A human finding is recorded by a reviewer, so its class is the reviewer's from the start."""
    if kind == "human_finding":
        dec = cur.get("decision") or "confirmed"
        by = cur.get("actor") or finding.get("created_by")
        return {"status": {"confirmed": "recorded_by_reviewer"}.get(dec, dec), "class": finding["category"], "ai_class": None,
                "by": by, "at": cur.get("at") or finding.get("created_at"), "comment": cur.get("comment")}
    dec = cur.get("decision") or "pending"
    corrected = cur.get("corrected_class_id")
    status = {"pending": "not_reviewed", "rejected": "rejected", "further_review": "further_review"}.get(dec, "corrected" if corrected else "confirmed")
    return {"status": status, "class": corrected or (ai_class if dec == "confirmed" else None), "ai_class": ai_class,
            "by": cur.get("actor"), "at": cur.get("at"), "comment": cur.get("comment")}


def _input_issues(det: dict) -> list[str]:
    out = []
    if det.get("screening_failed"):
        out.append("The aircraft-surface screening failed, so relevance of the image was not established.")
    if det.get("relevance_uncertain"):
        out.append("The aircraft-surface check found only weak evidence that this image shows an aircraft surface.")
    if det.get("overridden"):
        out.append("The aircraft-surface check rejected this image and a reviewer ran detection anyway.")
    return out


def _context(insp: dict) -> list[dict]:
    rows = [("Aircraft", insp.get("aircraft_ref")), ("Component", None if insp.get("component") in (None, "", "other") else insp.get("component")),
            ("Location / zone", insp.get("zone"))]
    return [{"field": k, "value": v, "established": bool(v), "display": v if v else NOT_ESTABLISHED} for k, v in rows] + [
        {"field": "Structural features (frame, stringer, fastener row, edge, joint)", "value": None, "established": False,
         "display": NOT_ESTABLISHED + " (VisionTra does not infer structure from an image)"},
        {"field": "Pressurized area", "value": None, "established": False, "display": NOT_ESTABLISHED},
        {"field": "Material and thickness", "value": None, "established": False, "display": NOT_ESTABLISHED},
        {"field": "Previous damage or repair", "value": None, "established": False, "display": NOT_ESTABLISHED + " (not linked to aircraft records)"}]


def _disposition(insp: dict, review: dict) -> dict:
    aircraft, component = bool(insp.get("aircraft_ref")), insp.get("component") not in (None, "", "other")
    reviewed = review["status"] in ("confirmed", "corrected", "recorded_by_reviewer")
    req = [
        {"item": "Inspector classification", "met": reviewed,
         "state": {"not_reviewed": "AI proposal not yet reviewed", "rejected": "Rejected by the reviewer", "further_review": "Further investigation requested"}.get(review["status"], "Recorded")},
        {"item": "Aircraft identity", "met": aircraft, "state": "Recorded" if aircraft else "Not recorded: the applicable manual cannot be identified"},
        {"item": "Component and exact location", "met": component and bool(insp.get("zone")),
         "state": "Recorded" if component and insp.get("zone") else NOT_ESTABLISHED},
        {"item": "Applicable aircraft-specific data (SRM, AMM or other approved data) and revision", "met": False, "state": "Not held by VisionTra: not established"},
        {"item": "Measurements taken under the applicable procedure", "met": False, "state": "Not recorded in VisionTra"},
        {"item": "Engineering assessment against the applicable limits", "met": False, "state": "Outside VisionTra: not established"},
    ]
    return {"status": "not_established", "label": "Engineering disposition not established",
            "statement": G.DISPOSITION_GATE + " Acceptance limits and repair disposition have not been established for this finding.",
            "requirements": req}


def _finding(f: dict, kind: str, out: dict, preds: list[dict], tiers: dict | None, thresholds: dict | None, media: dict) -> dict:
    cur = (out.get("current_decisions") or {}).get(f"{kind}:{f['id']}", {})
    ai_class = f.get("class_id") if kind == "prediction" else None
    review = _review(kind, cur, ai_class, f)
    cls = review["class"] or ai_class or "other"
    det = out.get("detection") or {}
    a = out.get("analysis") or {}
    box = f["box"]
    W, H = media.get("width"), media.get("height")
    obs = []
    if kind == "prediction":
        obs.append(f"The model marked a region in the {_where(box)}, covering about {max(box['w'] * box['h'] * 100, 0.1):.1f}% of the image"
                   + (f" (about {round(box['w'] * W)} × {round(box['h'] * H)} px of the {W} × {H} px original)." if W and H else "."))
    else:
        obs.append(f"A reviewer marked a region in the {_where(box)}" + (f" (about {round(box['w'] * W)} × {round(box['h'] * H)} px of the {W} × {H} px original)." if W and H else "."))
    if f.get("clipped"):
        obs.append("The region touches the image edge, so the finding may continue outside the frame.")
    obs.append("VisionTra does not measure surface geometry: whether the region shows a depression, crease, scratch, opening or coating damage "
               "has not been assessed by the system and is for the reviewer to judge from the image.")
    if review["status"] in ("confirmed", "corrected"):
        who = (review.get("by") or {}).get("name") or "A reviewer"
        obs.append(f"{who} {'corrected the class to' if review['status'] == 'corrected' else 'confirmed the class'} "
                   f"'{CLASS_LABEL.get(cls, cls)}'{' (AI proposed ' + CLASS_LABEL.get(ai_class, ai_class) + ')' if review['status'] == 'corrected' else ''}. "
                   "This is a classification, not a measurement or a disposition.")

    # same-class findings on the image (descriptive; never a cause or severity)
    same = [p for p in preds if p["id"] != f["id"] and p["media_id"] == f.get("media_id") and p["class"] == cls and p["decision"] != "rejected"]
    if same:
        obs.append(f"{len(same) + 1} findings of class '{CLASS_LABEL.get(cls, cls)}' (AI proposals or reviewer findings, not rejected) are on this image."
                   + (" Several similar findings do not by themselves establish a cause such as hail." if cls == "dent" else ""))

    assessment = None
    if kind == "prediction":
        competing = [{"id": p["id"], "class": p["ai_class"], "score": p["score"], "overlap_iou": round(_iou(box, p["box"]), 2)}
                     for p in preds if p["kind"] == "prediction" and p["id"] != f["id"] and p["media_id"] == f.get("media_id")
                     and p["ai_class"] != ai_class and _iou(box, p["box"]) >= _OVERLAP]
        th = ((thresholds or {}).get("score_threshold") or {}).get(ai_class)
        rv = ((thresholds or {}).get("review_threshold") or {}).get(ai_class)
        tier = _tier(tiers, ai_class, f["score"])
        issues = _input_issues(det)
        status = ("input_inadequate" if issues else "ambiguous" if competing else
                  "low_score" if (f.get("low_confidence") or (tier and tier["tier"] == "unlikely")) else "provisional")
        assessment = {
            "predicted_class": ai_class, "score": f["score"], "score_semantics": SCORE_SEMANTICS,
            "score_threshold": th, "review_threshold": rv, "low_confidence": bool(f.get("low_confidence")), "validation_tier": tier,
            "competing": competing,
            "competing_note": ("Other-class proposals overlap this region, so the model does not separate the classes here." if competing else
                               "No overlapping proposal of another class. The model keeps only its top class per box, so scores for other classes are not available."),
            "status": status,
            "status_label": {"input_inadequate": "Uncertain: image relevance or quality not established", "ambiguous": "Ambiguous: competing classes on the same area",
                             "low_score": "Uncertain: low model score", "provisional": "Provisional AI classification"}[status],
            "limitations": issues + [
                "The detector's held-out test precision and recall for this class are low (see the model card); a missing detection never means the surface is free of defects.",
                "Class mappings are provisional and the training labels were not verified by a qualified inspector."],
            "model_id": a.get("model_id"), "model_version": (a.get("model") or {}).get("version"), "analysed_at": a.get("finished_at"),
        }
        if cls == "dent" and "crack" in {c["class"] for c in competing}:
            obs.append("An overlapping 'crack or skin rupture' proposal exists; neither a crack nor its absence is established by the model.")

    g = G.for_class(cls)
    evidence = []
    if assessment and assessment["status"] == "input_inadequate":
        evidence.append("First establish that the image shows the aircraft surface in question, and re-photograph if it is unclear; the AI result is not reliable until then.")
    if assessment and assessment["status"] == "ambiguous":
        evidence.append("Competing classes overlap here: close-up images under different lighting and angles may help the reviewer separate them; photographs alone cannot establish material integrity.")
    evidence += g["immediate_actions"]

    unknowns = list(g["unknowns"])
    if not out.get("aircraft_ref"):
        unknowns.append("Not recorded in VisionTra: aircraft identity, so the applicable maintenance data cannot be identified")
    if out.get("component") in (None, "", "other") or not out.get("zone"):
        unknowns.append("Not recorded in VisionTra: the exact structural location (not inferred from the image)")

    return {
        "finding_id": f["id"], "source": "ai_prediction" if kind == "prediction" else "human_finding",
        "identity": {"finding_id": f["id"], "inspection_ref": out.get("ref"), "class_shown": cls,
                     "class_basis": "inspector" if review["status"] in ("confirmed", "corrected", "recorded_by_reviewer") else "ai_proposal",
                     "model_id": a.get("model_id") if kind == "prediction" else None, "analysed_at": a.get("finished_at") if kind == "prediction" else None,
                     "context": _context(out)},
        "stages": {"ai": ({"class": ai_class, "score": f["score"], "at": f.get("predicted_at")} if kind == "prediction" else None),
                   "inspector": review, "disposition": _disposition(out, review)},
        "observation": {"statements": obs, "basis": "Stored box geometry, image metadata and review records. No surface geometry is measured."},
        "assessment": assessment,
        "mechanism": {"statement": "Not established from the supplied evidence.", "background": g.get("mechanisms") or []},
        "unknowns": unknowns,
        "evidence_steps": evidence,
        "references": [{**s, "supports": s["supports"]} for s in g["sources"]],
    }


def build(out: dict) -> dict:
    """{finding_id: proposal} for every prediction and live human finding of a serialised inspection (``full_out``)."""
    a = out.get("analysis") or {}
    model_id = a.get("model_id")
    tiers = _bundle_json(model_id, "display_tiers.json")
    tiers = tiers if (tiers or {}).get("model_id") == model_id else None
    # The bundle on disk may have changed since this run. Missing historical
    # class thresholds stay unknown; scalar minima are not class thresholds.
    recorded = a.get("thresholds") or {}
    thresholds = {"score_threshold": recorded.get("score_per_class"),
                  "review_threshold": recorded.get("review_per_class")}
    cur = out.get("current_decisions") or {}
    media = {m["id"]: m for m in out.get("media") or []}
    flat = []
    for p in out.get("predictions") or []:
        c = cur.get(f"prediction:{p['id']}", {})
        flat.append({"id": p["id"], "kind": "prediction", "media_id": p["media_id"], "box": p["box"], "ai_class": p["class_id"], "score": p["score"],
                     "class": c.get("corrected_class_id") or p["class_id"], "decision": c.get("decision") or "pending"})
    for h in out.get("human_findings") or []:
        c = cur.get(f"human_finding:{h['id']}", {})
        flat.append({"id": h["id"], "kind": "human_finding", "media_id": h["media_id"], "box": h["box"], "ai_class": None, "score": None,
                     "class": h["category"], "decision": c.get("decision") or "confirmed"})
    res = {}
    for p in out.get("predictions") or []:
        res[p["id"]] = _finding(p, "prediction", out, flat, tiers, thresholds, media.get(p["media_id"], {}))
    for h in out.get("human_findings") or []:
        res[h["id"]] = _finding(h, "human_finding", out, flat, tiers, thresholds, media.get(h["media_id"], {}))
    return res
