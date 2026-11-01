"""Detection state vs. review state (Phase 15).

An inspection carries two independent questions:

* **Detection** — what the AI pipeline did with the image. Derived from the most recent analysis attempt:
  ``not_analysed`` (no model ran: none installed, or saved for manual inspection) · ``analysing`` ·
  ``relevance_rejected`` (the aircraft-surface check judged the image out of domain; the detector did NOT run) ·
  (an image the check is *uncertain* about is analysed and carries ``relevance_uncertain``) ·
  ``failed`` · ``completed_candidates`` · ``completed_zero`` (the detector ran and returned no candidates at the
  configured thresholds — not proof that the surface is defect-free).
* **Review** — what the people did: ``not_reviewed`` · ``in_review`` · ``signed_off``.

A gate rejection is never a completed analysis. Older databases stored gate rejections as runs with
``status='succeeded'`` plus ``suppressed.out_of_domain``; :func:`is_detection_run` treats those correctly.
Signing off without a completed detection requires the explicit ``manual_only`` acknowledgement, and signing off a
zero-candidate analysis with nothing recorded requires ``visual_check`` — both are stored with the sign-off event.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AnalysisRun, Inspection

ACKS = {"manual_only": "Manual-only inspection: no completed AI detection; the engineer inspected the image without AI candidates.",
        "visual_check": "Zero AI candidates: the engineer examined the image and recorded no defects."}


def is_out_of_domain(run: AnalysisRun | None) -> bool:
    return bool(run is not None and (run.error_code == "OUT_OF_DOMAIN" or (run.suppressed or {}).get("out_of_domain")))


def is_detection_run(run: AnalysisRun | None) -> bool:
    """True only for a run in which the detector actually produced output."""
    return run is not None and run.status == "succeeded" and not is_out_of_domain(run)


def last_attempt(db: Session, insp_id: str) -> AnalysisRun | None:
    return db.scalar(select(AnalysisRun).where(AnalysisRun.inspection_id == insp_id).order_by(AnalysisRun.attempt.desc()).limit(1))


def was_overridden(db: Session, insp_id: str) -> bool:
    """An engineer has already overridden the aircraft-surface check for this inspection (re-runs keep the override)."""
    for r in db.scalars(select(AnalysisRun).where(AnalysisRun.inspection_id == insp_id)):
        if ((r.suppressed or {}).get("domain_gate") or {}).get("overridden"):
            return True
    return False


def detection(db: Session, insp: Inspection, n_predictions: int, detection_run: AnalysisRun | None) -> dict:
    last = last_attempt(db, insp.id)
    gate = ((last.suppressed or {}).get("domain_gate") if last else None) or None
    if insp.status == "analysing":
        st = "analysing"
    elif last is None:
        st = "not_analysed"
    elif is_out_of_domain(last):
        st = "relevance_rejected"
    elif last.status in ("failed", "cancelled"):
        st = "failed"
    elif is_detection_run(last):
        st = "completed_candidates" if n_predictions else "completed_zero"
    else:
        st = "not_analysed"
    label = {
        "not_analysed": "Not analysed by the detection model",
        "analysing": "Detection running",
        "relevance_rejected": "Not analysed: the aircraft-surface check rejected this image",
        "failed": "Detection failed",
        "completed_candidates": f"{n_predictions} AI candidate{'s' if n_predictions != 1 else ''} to review",
        "completed_zero": "Detection ran: no candidates at the configured thresholds",
    }[st]
    out = {"state": st, "label": label, "completed": st in ("completed_candidates", "completed_zero"),
           "attempt": last.attempt if last else None, "gate": gate,
           "overridden": bool((gate or {}).get("overridden")),
           "error_code": (last.error_code if last and st == "failed" else None),
           "shown_run_attempt": detection_run.attempt if detection_run else None}
    # AI recovery: a three-state gate (VT-GATE-0002+) marks weak-evidence images "uncertain". They ARE analysed;
    # the flag asks the engineer to confirm the image shows an aircraft surface before relying on the result.
    screening_failed = (gate or {}).get("state") in ("failed", "error")
    out["relevance"] = ("failed" if screening_failed else "irrelevant" if st == "relevance_rejected" else (gate or {}).get("decision") or ("relevant" if gate else None))
    out["screening_failed"] = screening_failed
    if screening_failed:
        out["relevance_note"] = "Aircraft-surface screening failed. Relevance was not established; confirm the image manually before relying on detector results."
    out["relevance_uncertain"] = out["relevance"] == "uncertain"
    if out["relevance_uncertain"]:
        out["relevance_note"] = ("The aircraft-surface check was not sure this image shows an aircraft surface "
                                 f"({(gate or {}).get('reason', 'weak evidence')}). Detection ran anyway; confirm the image is relevant.")
    if st == "relevance_rejected" and detection_run is not None:
        out["note"] = (f"The latest attempt was rejected by the aircraft-surface check; the findings shown come from attempt "
                       f"{detection_run.attempt}, where detection did run.")
    return out


def review(insp: Inspection) -> dict:
    st = {"reviewed": "signed_off", "in_review": "in_review"}.get(insp.status, "not_reviewed")
    return {"state": st, "label": {"signed_off": "Signed off", "in_review": "In review", "not_reviewed": "Not reviewed"}[st]}


def required_ack(det: dict, n_human: int) -> str | None:
    """The acknowledgement a sign-off needs, or None."""
    if not det["completed"]:
        return "manual_only"
    if det["state"] == "completed_zero" and n_human == 0:
        return "visual_check"
    return None
