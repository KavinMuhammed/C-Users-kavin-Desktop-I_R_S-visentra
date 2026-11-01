"""Read-only evidence handoff. Hashes detect changes, not authorship or truth.

All runs and original predictions are exported alongside review events. The
current record is explicitly distinguished from historical reports. No inference,
model loading, label promotion, media mutation or engineering decision occurs.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from ..errors import api_error
from ..models import AnalysisRun, DefectPrediction, FindingDecision, HumanFinding, Inspection, ModelVersion, User
from . import analysis as AN, review as RV, service as SVC


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def build(db, user, inspection_id: str) -> dict:
    insp, role = SVC.inspection_for_user(db, inspection_id, user)
    revision = insp.revision
    current = SVC.full_out(db, insp, role)
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.inspection_id == insp.id)
                      .order_by(AnalysisRun.attempt, AnalysisRun.id)).all()
    predictions = db.scalars(select(DefectPrediction).where(DefectPrediction.inspection_id == insp.id)
                             .order_by(DefectPrediction.analysis_run_id, DefectPrediction.ordinal, DefectPrediction.id)).all()
    events = db.scalars(select(FindingDecision).where(FindingDecision.inspection_id == insp.id)
                        .order_by(FindingDecision.inspection_revision, FindingDecision.id)).all()
    findings = db.scalars(select(HumanFinding).where(HumanFinding.inspection_id == insp.id)
                          .order_by(HumanFinding.created_at, HumanFinding.id)).all()
    actor_ids = {e.actor_id for e in events} | {h.created_by for h in findings}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(actor_ids)))} if actor_ids else {}
    versions = {m.id: m for m in db.scalars(select(ModelVersion).where(
        ModelVersion.id.in_({r.model_version_id for r in runs})))} if runs else {}
    run_by_id = {r.id: r for r in runs}
    run_records = []
    for run in runs:
        record = AN.run_out(run, versions.get(run.model_version_id), sum(p.analysis_run_id == run.id for p in predictions))
        record["taxonomy_version"] = run.taxonomy_version
        run_records.append(record)
    checks = [
        {"id": "aircraft_identity", "recorded": bool((insp.aircraft_ref or "").strip()), "needed": "Record aircraft identity."},
        {"id": "component_location", "recorded": insp.component not in (None, "", "other") and bool((insp.zone or "").strip()),
         "needed": "Record component and aircraft-specific location; image coordinates do not establish location."},
        {"id": "review_decisions", "recorded": current["counts"]["pending"] == 0 and current["counts"]["further_review"] == 0 and insp.status == "reviewed",
         "needed": "Resolve outstanding review and record sign-off; no detections does not establish no defects."},
        {"id": "applicable_data", "recorded": False, "needed": "Establish applicability and revision of aircraft-specific technical data outside this checklist."},
        {"id": "measurements", "recorded": False, "needed": "Obtain measurements under the applicable procedure; photographs do not establish depth."},
        {"id": "engineering_disposition", "recorded": False, "needed": "Qualified personnel must determine disposition against applicable data outside VisionTra."},
    ]
    payload = {
        "schema": "visiontra.evidence-handoff/1",
        "inspection": {k: current[k] for k in ("id", "ref", "revision", "status", "aircraft_ref", "component", "zone", "notes",
            "created_at", "updated_at", "created_by", "signed_off_at", "signed_off_by", "signoff_note", "signoff_outcome")},
        "media": current["media"], "analysis_runs": run_records,
        "original_predictions": [RV.prediction_out(p, run_by_id.get(p.analysis_run_id), versions.get(p.model_version_id)) for p in predictions],
        "human_findings": [RV.human_finding_out(h, users) for h in findings],
        "review_events": [RV.event_out(e, users) for e in events],
        "current_decisions": current["current_decisions"], "current_detection": current["detection"],
        "reports": current["reports"],
        "evidence_checklist": {"policy": "VT-EVIDENCE-1", "checks": checks,
            "meaning": "Recorded fields only, not verified correctness, inspection coverage, regulatory compliance or readiness for flight. Free-text notes and OCR are not treated as verified technical data."},
        "limitations": [
            "Export of the current inspection revision, not a reconstruction of every historical inspection field.",
            "All stored analysis attempts are included. Current decisions may include superseded prediction targets; use analysis_run_id to distinguish runs.",
            "Original images and report bytes are referenced, not embedded or re-verified during export. Their recorded hashes are not a fresh integrity check.",
            "Weights and historical runtime/preprocessing implementations are not embedded; this packet alone cannot reproduce inference.",
            "Model scores are uncalibrated. Human decisions are recorded judgements, not independent ground truth or approved training labels.",
            "No engineering disposition or whole-aircraft coverage is established. A missing detection does not prove absence of a defect.",
        ],
    }
    # Fail rather than label a mixed concurrent read as one revision.
    latest = db.scalar(select(Inspection.revision).where(Inspection.id == insp.id))
    if latest != revision:
        raise api_error(409, "REVIEW_CONFLICT", "Inspection changed during export. Reload and export again.")
    return {"payload": payload, "integrity": {"algorithm": "sha256", "sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        "canonicalization": "UTF-8 JSON; sorted keys; separators comma and colon; ensure_ascii=false; allow_nan=false",
        "meaning": "Checksum of payload only. Not a digital signature, proof of authorship or verification that the evidence is true."}}
