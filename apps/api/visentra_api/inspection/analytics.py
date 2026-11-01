"""EP-31 inspection analytics (Phase 12): counts computed on the server from persisted records only.

Every figure is defined in `DEFINITIONS` and comes from the database: saved, non-deleted inspections in the caller's
workspaces where their role has `inspection.read`. Browser session records and SAMPLE records are never included
(they never reach the server). Review figures use the same event fold as the inspection API (`review.states_batch`).
No accuracy, time-saved or readiness figure is produced: there is no evidence from which to compute one.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import permissions as P
from ..models import COMPONENTS, ExperimentalRun, Inspection, InspectionReport, User, WorkspaceMember
from ..security import workspace_for_user
from . import review as RV
from . import service as SVC

DEFINITIONS = {
    "inspections": "Saved inspections (not deleted) in your workspaces where your role can read inspections.",
    "awaiting_review": "Saved inspections with status awaiting_review or analysis_failed: no review event yet.",
    "in_review": "Saved inspections with status in_review: review started, not signed off.",
    "reviewed": "Saved inspections signed off by a reviewer (status reviewed).",
    "findings": "Live findings: operational AI predictions of the latest successful analysis plus reviewer findings that were not deleted.",
    "pending_findings": "Live findings whose current decision is pending (only AI predictions can be pending).",
    "confirmed_by_category": "Confirmed findings per defect category (a reclassified prediction counts in its corrected class).",
    "reports": "Server reports with status available; stale = the inspection changed after the report's revision.",
    "experimental_runs": "Development-only experimental model runs on saved images. Never findings; never counted above.",
    "timeline": "Inspections created per UTC day over the last 30 days.",
    "attention": "Up to 8 inspections not signed off, most pending findings first, then most recently changed.",
}


def _scope(db: Session, user: User, workspace_id: str | None) -> list[str]:
    ws_ids = [workspace_for_user(db, workspace_id, user).id] if workspace_id else SVC.member_workspace_ids(db, user)
    roles = dict(db.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(WorkspaceMember.user_id == user.id)).all())
    return [w for w in ws_ids if P.workspace_can(roles.get(w), "inspection.read")]


def summary(db: Session, user: User, workspace_id: str | None = None) -> dict:
    ws = _scope(db, user, workspace_id)
    rows = list(db.scalars(select(Inspection).where(Inspection.workspace_id.in_(ws), Inspection.deleted_at.is_(None)))) if ws else []
    by_status = Counter(i.status for i in rows)
    by_component = Counter(i.component for i in rows)
    states = RV.states_batch(db, rows)
    dec, cat, comp_def, comp_pred = Counter(), Counter(), Counter(), Counter()
    src = Counter()
    pending_by: dict[str, int] = {}
    for i in rows:
        st = states[i.id]
        counts, _ = RV.counts_and_categories(st)
        pending_by[i.id] = counts["pending"]
        src["ai"] += counts["predictions"]
        src["reviewer"] += counts["human_findings"]
        for k in ("pending", "confirmed", "rejected", "further_review"):
            dec[k] += counts[k]
        cur = st["current"]
        comp_pred[i.component] += len(st["predictions"])
        for p in st["predictions"]:
            c = cur.get(f"prediction:{p.id}", {})
            if c.get("decision") == "confirmed":
                cat[c.get("corrected_class_id") or p.class_id] += 1
                comp_def[i.component] += 1
        for h in st["human_findings"]:
            if cur.get(f"human_finding:{h.id}", {}).get("decision") == "confirmed":
                cat[h.category] += 1
                comp_def[i.component] += 1
    ids = [i.id for i in rows]
    reps = {"available": 0, "stale": 0, "failed": 0}
    exp_runs = 0
    rev = {i.id: i.revision for i in rows}
    for part in RV._chunks(ids):
        for r in db.scalars(select(InspectionReport).where(InspectionReport.inspection_id.in_(part), InspectionReport.status.in_(("available", "failed")))):
            if r.status == "failed":
                reps["failed"] += 1
            else:
                reps["available"] += 1
                reps["stale"] += int(rev[r.inspection_id] > r.inspection_revision)
        exp_runs += db.scalar(select(func.count()).select_from(ExperimentalRun).where(ExperimentalRun.inspection_id.in_(part))) or 0
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=d) for d in range(29, -1, -1)]
    created = Counter(SVC.iso(i.created_at)[:10] for i in rows)
    timeline = [{"date": d.isoformat(), "created": created.get(d.isoformat(), 0)} for d in days]
    open_rows = [i for i in rows if i.status != "reviewed"]
    open_rows.sort(key=lambda i: (-pending_by[i.id], -(i.updated_at or i.created_at).timestamp()))
    attention = [{"id": i.id, "ref": i.ref, "status": i.status, "component": i.component, "pending": pending_by[i.id],
                  "updated_at": SVC.iso(i.updated_at or i.created_at)} for i in open_rows[:8]]
    return {
        "scope": {"workspaces": len(ws), "source": "server", "includes_sample_data": False},
        "counts": {"inspections": len(rows), "awaiting_review": by_status["awaiting_review"] + by_status["analysis_failed"],
                   "in_review": by_status["in_review"], "reviewed": by_status["reviewed"], "analysing": by_status["analysing"]},
        "by_status": dict(by_status), "by_component": {c: by_component[c] for c in COMPONENTS if by_component[c]},
        "findings": {"total": src["ai"] + src["reviewer"], "ai_predictions": src["ai"], "reviewer_findings": src["reviewer"],
                     "pending_findings": dec["pending"], "by_decision": dict(dec), "confirmed_by_category": dict(cat),
                     "confirmed_by_component": {c: comp_def[c] for c in COMPONENTS if comp_def[c]},
                     "ai_predictions_by_component": {c: comp_pred[c] for c in COMPONENTS if comp_pred[c]}},
        "reports": reps, "experimental_runs": exp_runs, "timeline": timeline, "attention": attention,
        "generated_at": SVC.iso(datetime.now(timezone.utc)), "definitions": DEFINITIONS,
    }
