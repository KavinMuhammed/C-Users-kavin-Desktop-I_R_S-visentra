"""Human review of saved inspections (Phase 11; Phase 5 C-REVIEW, ADR-008).

Model
  · defect_predictions   immutable AI proposals (operational models only; none exist yet - R3). Never updated by review.
  · human_findings       reviewer-drawn regions. Created and deleted only through events; the row is never removed.
  · finding_decisions    append-only events. EVERY state-changing review action bumps inspections.revision (through
                         lifecycle.py, conditional on the caller's If-Match revision) and writes exactly one event that
                         carries the new revision; UNIQUE(inspection_id, inspection_revision) is the database-level guard.
  · The current decision of a target is folded from its events in revision order; history is never rewritten.

Status (catalog state machine inspections.status, applied only through lifecycle.py):
  awaiting_review | analysis_failed --first event--> in_review --sign-off (no pending)--> reviewed
  reviewed --reopen, or any decision change--> in_review  (sign-off fields cleared; the earlier sign-off stays in history)

Experimental runs (ADR-014) are not review targets: their suggestions are not defect_predictions rows, so a decision on one
is refused as an unknown target, and the inspection is never changed by them.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import errors as E
from .. import permissions as P
from ..audit import audit
from ..errors import api_error
from ..logs import request_id_var
from ..models import (REVIEW_CATEGORIES, AnalysisRun, DefectPrediction, FindingDecision, HumanFinding, Inspection, InspectionMedia, InspectionReport,
                      ModelVersion, User, now, uid)
from ..security import client_ip
from ..services import idempotency as IDEM
from . import lifecycle as L
from . import service as SVC

TAXONOMY_VERSION = "TAX-1.0"
COMMENT_MAX = 600
SIGNOFF_NOTE_MAX = 300
REVIEWABLE = ("awaiting_review", "analysis_failed", "in_review", "reviewed")
DECISION_LABEL = {"confirmed": "Confirmed defect", "rejected": "Rejected prediction", "further_review": "Needs further investigation", "pending": "Pending review"}


def clean_text(v: Any, limit: int, field: str) -> str | None:
    """Untrusted reviewer text: str only, control characters removed, trimmed, length-limited. Never interpreted as markup."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise api_error(422, E.VALIDATION, f"{field} must be text.", field=field)
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
    if len(v) > limit:
        raise api_error(422, E.VALIDATION, f"{field} must be at most {limit} characters.", field=field)
    return v or None


from .states import is_detection_run  # noqa: E402


# ====================================================================== reading the review state
def latest_run(db: Session, insp_id: str) -> AnalysisRun | None:
    """The most recent run in which the detector actually ran (gate rejections are not detection runs)."""
    from .states import is_detection_run
    for r in db.scalars(select(AnalysisRun).where(AnalysisRun.inspection_id == insp_id, AnalysisRun.status == "succeeded")
                        .order_by(AnalysisRun.attempt.desc())):
        if is_detection_run(r):
            return r
    return None


def state(db: Session, insp: Inspection, up_to_revision: int | None = None) -> dict:
    """Fold the event history into the current (or, with up_to_revision, a past) review state. Pure read."""
    q = select(FindingDecision).where(FindingDecision.inspection_id == insp.id)
    if up_to_revision is not None:
        q = q.where(FindingDecision.inspection_revision <= up_to_revision)
    events = db.scalars(q.order_by(FindingDecision.inspection_revision)).all()
    run = latest_run(db, insp.id)
    preds = db.scalars(select(DefectPrediction).where(DefectPrediction.analysis_run_id == run.id).order_by(DefectPrediction.media_id, DefectPrediction.ordinal)).all() if run else []
    findings = {h.id: h for h in db.scalars(select(HumanFinding).where(HumanFinding.inspection_id == insp.id).order_by(HumanFinding.created_at))}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({e.actor_id for e in events} | {h.created_by for h in findings.values()})))} if (events or findings) else {}
    return _fold(events, run, preds, findings, users)


def _fold(events, run, preds, findings: dict, users: dict) -> dict:
    """The pure part of `state`: events (ordered by revision) → current decision per target. Shared with states_batch."""
    cur: dict[str, dict] = {}
    created_ids: set[str] = set()
    deleted: dict[str, FindingDecision] = {}
    for e in events:
        if e.target_type == "inspection":
            continue
        key = f"{e.target_type}:{e.prediction_id or e.human_finding_id}"
        c = cur.setdefault(key, {"decision": "pending" if e.target_type == "prediction" else None, "corrected_class_id": None, "comment": None})
        if e.target_type == "human_finding":
            created_ids.add(e.human_finding_id)
        if e.kind in ("decision", "reclassify"):
            c.update(decision=e.decision, corrected_class_id=e.corrected_class_id if e.kind == "reclassify" else None,
                     actor={"id": e.actor_id, "name": users[e.actor_id].name if e.actor_id in users else None}, at=SVC.iso(e.at), revision=e.inspection_revision)
            if e.comment:
                c["comment"] = e.comment
        elif e.kind == "note":
            c["comment"] = e.comment
        elif e.kind == "delete":
            deleted[e.human_finding_id] = e
    live = [h for hid, h in findings.items() if hid in created_ids and hid not in deleted]
    for p in preds:
        cur.setdefault(f"prediction:{p.id}", {"decision": "pending", "corrected_class_id": None, "comment": None})
    # signed-off state at that revision (for reports pinned to a past revision the inspection row may have moved on)
    return {"events": events, "run": run, "predictions": preds, "human_findings": live, "deleted_findings": [findings[h] for h in deleted if h in findings],
            "current": cur, "users": users}


_CHUNK = 500      # bound IN-lists (SQLite parameter limits, PostgreSQL plan size)


def _chunks(ids: list[str]):
    for k in range(0, len(ids), _CHUNK):
        yield ids[k:k + _CHUNK]


def states_batch(db: Session, insps: list[Inspection]) -> dict[str, dict]:
    """Phase 12 (P-01/P-02): current review state of many inspections in four queries per 500 inspections instead of
    four per inspection. Same fold as `state`; actor names are not loaded (callers use counts and categories only)."""
    ids = [i.id for i in insps]
    events: dict[str, list] = {i: [] for i in ids}
    runs: dict[str, AnalysisRun] = {}
    found: dict[str, dict] = {i: {} for i in ids}
    preds: dict[str, list] = {}
    for part in _chunks(ids):
        for e in db.scalars(select(FindingDecision).where(FindingDecision.inspection_id.in_(part))
                            .order_by(FindingDecision.inspection_id, FindingDecision.inspection_revision)):
            events[e.inspection_id].append(e)
        for r in db.scalars(select(AnalysisRun).where(AnalysisRun.inspection_id.in_(part), AnalysisRun.status == "succeeded")):
            if not is_detection_run(r):
                continue
            if r.inspection_id not in runs or r.attempt > runs[r.inspection_id].attempt:
                runs[r.inspection_id] = r
        for h in db.scalars(select(HumanFinding).where(HumanFinding.inspection_id.in_(part)).order_by(HumanFinding.created_at)):
            found[h.inspection_id][h.id] = h
    run_ids = [r.id for r in runs.values()]
    for part in _chunks(run_ids):
        for p in db.scalars(select(DefectPrediction).where(DefectPrediction.analysis_run_id.in_(part)).order_by(DefectPrediction.media_id, DefectPrediction.ordinal)):
            preds.setdefault(p.analysis_run_id, []).append(p)
    out = {}
    for i in ids:
        run = runs.get(i)
        out[i] = _fold(events[i], run, preds.get(run.id, []) if run else [], found[i], {})
    return out


def _box(o) -> dict:
    return {"x": o.box_x, "y": o.box_y, "w": o.box_w, "h": o.box_h}


def prediction_out(p: DefectPrediction, run: AnalysisRun | None, mv: ModelVersion | None) -> dict:
    return {"id": p.id, "media_id": p.media_id, "ordinal": p.ordinal, "class_id": p.class_id, "score": p.score, "low_confidence": p.low_confidence,
            "box": _box(p), "clipped": p.clipped, "model_version_id": p.model_version_id, "model_version": (f"v{mv.version}" if mv else None),
            "taxonomy_version": p.taxonomy_version, "predicted_at": SVC.iso(p.predicted_at), "analysis_run_id": p.analysis_run_id, "kind": "ai_prediction"}


def human_finding_out(h: HumanFinding, users: dict) -> dict:
    u = users.get(h.created_by)
    return {"id": h.id, "media_id": h.media_id, "category": h.category, "box": _box(h), "taxonomy_version": h.taxonomy_version,
            "created_by": {"id": h.created_by, "name": u.name if u else None}, "created_at": SVC.iso(h.created_at), "deleted_at": SVC.iso(h.deleted_at),
            "kind": "human_finding"}


def event_out(e: FindingDecision, users: dict) -> dict:
    u = users.get(e.actor_id)
    tid = e.prediction_id or e.human_finding_id
    return {"id": e.id, "target": {"type": e.target_type, "id": tid}, "kind": e.kind, "decision": e.decision, "decision_label": DECISION_LABEL.get(e.decision) if e.decision else None,
            "corrected_class_id": e.corrected_class_id, "comment": e.comment, "previous": e.previous,
            "actor": {"id": e.actor_id, "name": u.name if u else None}, "at": SVC.iso(e.at), "inspection_revision": e.inspection_revision}


def counts_and_categories(st: dict) -> tuple[dict, list[str]]:
    cur = st["current"]
    live_keys = [f"prediction:{p.id}" for p in st["predictions"]] + [f"human_finding:{h.id}" for h in st["human_findings"]]
    dec = [cur.get(k, {}).get("decision") for k in live_keys]
    cats = set()
    for p in st["predictions"]:
        c = cur.get(f"prediction:{p.id}", {})
        if c.get("decision") == "confirmed":
            cats.add(c.get("corrected_class_id") or p.class_id)
    for h in st["human_findings"]:
        if cur.get(f"human_finding:{h.id}", {}).get("decision") == "confirmed":
            cats.add(h.category)
    return ({"predictions": len(st["predictions"]), "human_findings": len(st["human_findings"]), "pending": dec.count("pending"),
             "confirmed": dec.count("confirmed"), "rejected": dec.count("rejected"), "further_review": dec.count("further_review")}, sorted(cats))


def review_permissions(role: str | None) -> dict:
    return {"review": P.workspace_can(role, "inspection.review"), "report_create": P.workspace_can(role, "report.create"),
            "report_read": P.workspace_can(role, "report.read")}


def enrich_full(db: Session, insp: Inspection, role: str, out: dict) -> None:
    """Fill the review fields of the Phase 5 Inspection schema (Phase 10 returned them empty). Additive: same shape."""
    st = state(db, insp)
    run = st["run"]
    mv = db.get(ModelVersion, run.model_version_id) if run else None
    counts, cats = counts_and_categories(st)
    out["counts"].update(counts)
    out["categories"] = cats
    out["predictions"] = [prediction_out(p, run, mv) for p in st["predictions"]]
    out["human_findings"] = [human_finding_out(h, st["users"]) for h in st["human_findings"]]
    out["current_decisions"] = {k: v for k, v in st["current"].items() if v.get("decision") is not None or v.get("comment")}
    if run:
        out["analysis"] = {"run_id": run.id, "attempt": run.attempt, "status": run.status, "model": {"version_id": run.model_version_id, "version": f"v{mv.version}" if mv else None,
                           "architecture": mv.architecture if mv else None, "taxonomy_version": run.taxonomy_version},
                           "thresholds": {"score": run.score_threshold, "iou": run.iou_threshold, "review": run.review_threshold,
                                          "score_per_class": (run.timings or {}).get("score_threshold_per_class"),
                                          "review_per_class": (run.timings or {}).get("review_threshold_per_class")},
                           "weights_sha256": run.model_checksum,
                           "requested_at": SVC.iso(run.requested_at), "finished_at": SVC.iso(run.finished_at), "error_code": run.error_code,
                           "model_id": (run.timings or {}).get("model_id"), "timings_ms": {k: v for k, v in (run.timings or {}).items() if k.endswith("_ms")},
                           "domain_gate": (run.suppressed or {}).get("domain_gate"), "out_of_domain": bool((run.suppressed or {}).get("out_of_domain"))}
    from . import states as ST
    out["detection"] = ST.detection(db, insp, len(st["predictions"]), run)
    out["review_state"] = ST.review(insp)
    out["signoff_requires"] = ST.required_ack(out["detection"], len(st["human_findings"])) if insp.status != "reviewed" else None
    signer = db.get(User, insp.signed_off_by) if insp.signed_off_by else None
    out["signed_off_by"] = {"id": signer.id, "name": signer.name} if signer else None
    from ..reports import service as RS          # local import: reports depend on review
    out["reports"] = RS.summaries(db, insp)
    out["has_report"] = any(r["status"] == "available" for r in out["reports"])
    out["report_stale"] = (all(r["stale"] for r in out["reports"] if r["status"] == "available") if out["has_report"] else None)
    out["review_permissions"] = review_permissions(role)
    from . import proposal as PR             # read-only: built from the fields above, never stored
    out["proposals"] = PR.build(out)
    out["persistence"] = {"inspection": True, "review": True, "reports": True,
                          "note": "The inspection, its original image, review decisions, reviewer findings, sign-off and server reports are stored on the server."}


def enrich_summaries(db: Session, rows: list[Inspection], items: list[dict]) -> None:
    """Counts, confirmed categories and report flags for history rows. Phase 12 (P-01): batched — a fixed number of
    queries per page instead of a state fold and a report query per row."""
    from ..models import InspectionReport
    states = states_batch(db, rows)
    reps: dict[str, list] = {}
    for part in _chunks([i.id for i in rows]):
        for r in db.scalars(select(InspectionReport).where(InspectionReport.inspection_id.in_(part), InspectionReport.status == "available")):
            reps.setdefault(r.inspection_id, []).append(r)
    for i, it in zip(rows, items):
        counts, cats = counts_and_categories(states[i.id])
        it["counts"].update(counts)
        it["categories"] = cats
        avail = reps.get(i.id, [])
        it["has_report"] = bool(avail)
        it["report_stale"] = all(i.revision > r.inspection_revision for r in avail) if avail else None


def category_inspection_ids(db: Session, category: str, workspace_ids: list[str] | None = None) -> set[str]:
    """Inspections with a CONFIRMED finding of `category` (human finding, or prediction confirmed/reclassified to it).
    Phase 12 (P-02): only inspections in the caller's workspaces that have a confirming event are folded, in one batch."""
    confirming = select(FindingDecision.inspection_id).where(FindingDecision.kind.in_(("decision", "reclassify")), FindingDecision.decision == "confirmed")
    q = select(Inspection).where(Inspection.id.in_(confirming), Inspection.deleted_at.is_(None))
    if workspace_ids is not None:
        if not workspace_ids:
            return set()
        q = q.where(Inspection.workspace_id.in_(workspace_ids))
    cands = list(db.scalars(q))
    return {iid for iid, st in states_batch(db, cands).items() if category in counts_and_categories(st)[1]}


# ====================================================================== writing (one event per revision)
def _guard(db: Session, insp_id: str, user: User, request: Request) -> tuple[Inspection, str, int]:
    insp, role = SVC.inspection_for_user(db, insp_id, user, "inspection.review")
    expected = SVC.parse_if_match(request)
    if insp.status not in REVIEWABLE:
        raise api_error(409, E.CONFLICT, f"This inspection cannot be reviewed while it is '{insp.status}'.", current_status=insp.status)
    return insp, role, expected


def _check_revision(insp: Inspection, expected: int) -> None:
    """After the idempotency replay check: a retried request whose first attempt succeeded gets its stored answer, not a conflict."""
    if insp.revision != expected:
        raise api_error(409, E.REVIEW_CONFLICT, "Someone else changed this inspection. Reload to see the latest decisions.",
                        current_revision=insp.revision, your_revision=expected, current_status=insp.status)


def _advance(db: Session, insp: Inspection, expected: int, to: str | None = None, fields: dict | None = None) -> int:
    """Bump the revision once (and change status when needed). Returns the new revision."""
    if to is None:
        # a review event: move into review, or out of a sign-off
        if insp.status in ("awaiting_review", "analysis_failed"):
            return L.transition(db, insp.id, "in_review", expected_revision=expected)
        if insp.status == "reviewed":
            return L.transition(db, insp.id, "in_review", expected_revision=expected,
                                fields={"signed_off_at": None, "signed_off_by": None, "signoff_outcome": None, "signoff_note": None})
        return L.update_fields(db, insp.id, expected, {})
    return L.transition(db, insp.id, to, expected_revision=expected, fields=fields)


def _commit_event(db: Session, request: Request, user: User, insp: Inspection, role: str, scope: str, rh: str, build, status_code: int = 200):
    """Run one review transaction (re-run on transient lock errors) and answer with the stored state."""
    def tx():
        ev, extra = build()
        body = {"revision": ev.inspection_revision, "event": event_out(ev, {user.id: user}), **extra}
        fresh = db.get(Inspection, insp.id)
        db.refresh(fresh)
        full = SVC.full_out(db, fresh, role)
        body["current_decisions"] = full["current_decisions"]
        body["inspection"] = full
        IDEM.claim(db, request, scope, status_code, body, rh)
        db.commit()
        return body
    try:
        body = SVC.write_retry(db, tx)
    except IntegrityError:
        db.rollback()
        rep = IDEM.replay(db, request, scope, rh)
        if rep is not None:
            return rep
        cur = db.get(Inspection, insp.id)
        raise api_error(409, E.REVIEW_CONFLICT, "Someone else changed this inspection. Reload to see the latest decisions.",
                        current_revision=cur.revision if cur else None) from None
    return JSONResponse(body, status_code=status_code, headers={"ETag": f'"{body["revision"]}"'})


def _event(insp: Inspection, user: User, revision: int, **kw) -> FindingDecision:
    return FindingDecision(id=uid(), inspection_id=insp.id, actor_id=user.id, at=now(), inspection_revision=revision, request_id=(request_id_var.get() or None), **kw)


def decide(db: Session, request: Request, user: User, insp_id: str, body: dict):
    """EP-12: decision | reclassify | note | delete on one prediction or reviewer finding."""
    insp, role, expected = _guard(db, insp_id, user, request)
    scope = f"user:{user.id}:review:{insp.id}"
    rh = IDEM.request_hash(request, {"if_match": expected, "body": body})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    _check_revision(insp, expected)
    target = body.get("target") if isinstance(body.get("target"), dict) else None
    if not target or target.get("type") not in ("prediction", "human_finding") or not isinstance(target.get("id"), str) or not (1 <= len(target["id"]) <= 64):
        raise api_error(422, E.VALIDATION, "target must be {type: 'prediction' | 'human_finding', id}.", field="target")
    kind = body.get("kind", "decision")
    if kind not in ("decision", "reclassify", "note", "delete"):
        raise api_error(422, E.VALIDATION, "kind must be decision, reclassify, note or delete.", field="kind")
    decision = body.get("decision")
    comment = clean_text(body.get("comment"), COMMENT_MAX, "comment")
    corrected = body.get("corrected_class_id")
    ttype, tid = target["type"], target["id"]
    # the target must exist on THIS inspection (and, for predictions, in its current analysis run)
    st = state(db, insp)
    if ttype == "prediction":
        if not any(p.id == tid for p in st["predictions"]):
            raise api_error(404, E.NOT_FOUND, "This prediction does not exist on this inspection.", field="target.id")
    elif not any(h.id == tid for h in st["human_findings"]):
        raise api_error(404, E.NOT_FOUND, "This reviewer finding does not exist on this inspection (or was deleted).", field="target.id")
    cur = st["current"].get(f"{ttype}:{tid}", {})
    fields: dict[str, Any] = {"target_type": ttype, "prediction_id": tid if ttype == "prediction" else None, "human_finding_id": tid if ttype == "human_finding" else None,
                              "kind": kind, "comment": comment, "previous": {"decision": cur.get("decision"), "corrected_class_id": cur.get("corrected_class_id")}}
    if kind == "decision":
        if decision not in ("pending", "confirmed", "rejected", "further_review"):
            raise api_error(422, E.VALIDATION, "decision must be confirmed, rejected, further_review or pending.", field="decision")
        if ttype == "human_finding" and decision == "pending":
            raise api_error(422, E.VALIDATION, "A reviewer finding cannot be set back to pending.", field="decision")
        if decision == cur.get("decision") and not cur.get("corrected_class_id"):
            raise api_error(409, E.CONFLICT, f"The decision is already '{decision}'.", field="decision")
        fields["decision"] = decision
    elif kind == "reclassify":
        if ttype != "prediction":
            raise api_error(422, E.VALIDATION, "Only AI predictions can be reclassified.", field="kind")
        if corrected not in REVIEW_CATEGORIES:
            raise api_error(422, E.VALIDATION, f"corrected_class_id must be one of {', '.join(REVIEW_CATEGORIES)}.", field="corrected_class_id")
        pred = next(p for p in st["predictions"] if p.id == tid)
        if corrected == pred.class_id:
            raise api_error(422, E.VALIDATION, "The corrected class equals the predicted class; confirm the prediction instead.", field="corrected_class_id")
        fields.update(decision="confirmed", corrected_class_id=corrected)
    elif kind == "note":
        if not comment:
            raise api_error(422, E.VALIDATION, "A note needs a comment.", field="comment")
    elif kind == "delete":
        if ttype != "human_finding":
            raise api_error(422, E.VALIDATION, "AI predictions cannot be deleted; reject them instead.", field="kind")
    if kind != "reclassify" and corrected is not None:
        raise api_error(422, E.VALIDATION, "corrected_class_id is only allowed with kind 'reclassify'.", field="corrected_class_id")
    if kind not in ("decision", "reclassify") and decision is not None:
        raise api_error(422, E.VALIDATION, "decision is only allowed with kind 'decision'.", field="decision")
    db.commit()     # end the read snapshot; the conditional revision bump starts the write transaction

    def build():
        rev = _advance(db, db.get(Inspection, insp.id), expected)
        ev = _event(insp, user, rev, **{k: v for k, v in fields.items()})
        db.add(ev)
        if kind == "delete":
            db.execute(HumanFinding.__table__.update().where(HumanFinding.id == tid, HumanFinding.deleted_at.is_(None)).values(deleted_at=now(), deleted_by=user.id))
        db.flush()
        audit(db, {"decision": "finding.decided", "reclassify": "finding.reclassified", "note": "finding.noted", "delete": "finding.deleted"}[kind],
              "inspection", insp.id, user_id=user.id, ip=client_ip(request),
              meta={"event_id": ev.id, "revision": rev, "target": {"type": ttype, "id": tid}, "decision": ev.decision, "corrected_class_id": ev.corrected_class_id,
                    "previous": fields["previous"], "comment_chars": len(comment or "")})
        return ev, {}
    return _commit_event(db, request, user, insp, role, scope, rh, build)


def decide_bulk(db: Session, request: Request, user: User, insp_id: str, body: dict):
    """Phase 15: one decision applied to several pending AI predictions in ONE request and ONE transaction
    ({"decision": "rejected", "targets": [prediction ids]}). Each target still gets its own event, revision and
    audit entry, exactly as if decided one by one; any invalid target refuses the whole batch. Replaces the
    client-side loop that stopped half-way when a request was already in flight."""
    insp, role, expected = _guard(db, insp_id, user, request)
    scope = f"user:{user.id}:review:{insp.id}"
    rh = IDEM.request_hash(request, {"if_match": expected, "body": body, "op": "bulk"})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    _check_revision(insp, expected)
    decision = body.get("decision")
    if decision not in ("confirmed", "rejected", "further_review"):
        raise api_error(422, E.VALIDATION, "decision must be confirmed, rejected or further_review.", field="decision")
    ids = body.get("targets")
    if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(t, str) and 1 <= len(t) <= 64 for t in ids) or len(set(ids)) != len(ids):
        raise api_error(422, E.VALIDATION, "targets must be a list of 1–500 distinct prediction ids.", field="targets")
    comment = clean_text(body.get("comment"), COMMENT_MAX, "comment")
    st = state(db, insp)
    known = {p.id for p in st["predictions"]}
    missing = [t for t in ids if t not in known]
    if missing:
        raise api_error(404, E.NOT_FOUND, f"{len(missing)} prediction(s) do not exist on this inspection.", field="targets", missing=missing[:20])
    not_pending = [t for t in ids if (st["current"].get(f"prediction:{t}") or {}).get("decision", "pending") != "pending"]
    if not_pending:
        raise api_error(409, E.CONFLICT, f"{len(not_pending)} prediction(s) already have a decision. Reload and try again.", field="targets", decided=not_pending[:20])
    db.commit()

    def build():
        rev, last = expected, None
        for tid in ids:
            cur = db.get(Inspection, insp.id); db.refresh(cur)
            rev = _advance(db, cur, rev)
            last = _event(insp, user, rev, target_type="prediction", prediction_id=tid, human_finding_id=None, kind="decision", decision=decision,
                          comment=comment, previous={"decision": "pending", "corrected_class_id": None, "bulk": True})
            db.add(last)
            db.flush()
            audit(db, "finding.decided", "inspection", insp.id, user_id=user.id, ip=client_ip(request),
                  meta={"event_id": last.id, "revision": rev, "target": {"type": "prediction", "id": tid}, "decision": decision, "bulk": True,
                        "batch_size": len(ids)})
        return last, {"decided": len(ids)}
    return _commit_event(db, request, user, insp, role, scope, rh, build)


def add_finding(db: Session, request: Request, user: User, insp_id: str, body: dict):
    """EP-13: a reviewer-drawn finding (human-originated; never an AI prediction). Starts confirmed."""
    insp, role, expected = _guard(db, insp_id, user, request)
    scope = f"user:{user.id}:review:{insp.id}"
    rh = IDEM.request_hash(request, {"if_match": expected, "body": body})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    _check_revision(insp, expected)
    media_id, category = body.get("media_id"), body.get("category")
    b = body.get("box") if isinstance(body.get("box"), dict) else None
    m = db.get(InspectionMedia, media_id) if isinstance(media_id, str) and len(media_id) <= 64 else None
    if m is None or m.inspection_id != insp.id:
        raise api_error(404, E.NOT_FOUND, "This image does not belong to this inspection.", field="media_id")
    if category not in REVIEW_CATEGORIES:
        raise api_error(422, E.VALIDATION, f"category must be one of {', '.join(REVIEW_CATEGORIES)}.", field="category")
    try:
        x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
    except Exception:
        raise api_error(422, E.VALIDATION, "box must be {x, y, w, h} as fractions of the image.", field="box") from None
    if not all(map(lambda v: v == v and abs(v) != float("inf"), (x, y, w, h))) or x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise api_error(422, E.VALIDATION, "The box must lie inside the image (x, y ≥ 0; w, h > 0; x + w ≤ 1; y + h ≤ 1).", field="box")
    comment = clean_text(body.get("comment"), COMMENT_MAX, "comment")
    db.commit()

    def build():
        rev = _advance(db, db.get(Inspection, insp.id), expected)
        hf = HumanFinding(id=uid(), inspection_id=insp.id, media_id=m.id, category=category, taxonomy_version=TAXONOMY_VERSION,
                          box_x=x, box_y=y, box_w=w, box_h=h, created_by=user.id, created_at=now())
        db.add(hf)
        db.flush()
        ev = _event(insp, user, rev, target_type="human_finding", human_finding_id=hf.id, kind="decision", decision="confirmed", comment=comment,
                    previous=None)
        db.add(ev)
        db.flush()
        audit(db, "finding.created", "inspection", insp.id, user_id=user.id, ip=client_ip(request),
              meta={"event_id": ev.id, "revision": rev, "human_finding_id": hf.id, "category": category, "comment_chars": len(comment or "")})
        return ev, {"finding": human_finding_out(hf, {user.id: user})}
    return _commit_event(db, request, user, insp, role, scope, rh, build, status_code=201)


def sign_off(db: Session, request: Request, user: User, insp_id: str, body: dict):
    """EP-15: refused while any prediction is pending (REVIEW_INCOMPLETE). Outcome is derived, never chosen."""
    insp, role, expected = _guard(db, insp_id, user, request)
    scope = f"user:{user.id}:review:{insp.id}"
    rh = IDEM.request_hash(request, {"if_match": expected, "body": body, "op": "signoff"})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    _check_revision(insp, expected)
    if insp.status == "reviewed":
        raise api_error(409, E.CONFLICT, "This inspection is already signed off.", current_status=insp.status)
    note = clean_text(body.get("note"), SIGNOFF_NOTE_MAX, "note")
    st = state(db, insp)
    pending = [k.split(":", 1)[1] for k, v in st["current"].items() if v.get("decision") == "pending"]
    if pending:
        raise api_error(409, E.REVIEW_INCOMPLETE, f"{len(pending)} finding(s) are still pending. Decide every finding before completing the review.",
                        pending_finding_ids=pending)
    counts, _ = counts_and_categories(st)
    outcome = "findings_recorded" if counts["confirmed"] or counts["further_review"] else "no_confirmed_findings"
    from . import states as ST
    det = ST.detection(db, insp, len(st["predictions"]), st["run"])
    need = ST.required_ack(det, len(st["human_findings"]))
    ack = body.get("acknowledgement") if isinstance(body.get("acknowledgement"), str) else None
    if need and ack != need:
        msg = ("The detection model has not completed an analysis of this image (" + det["label"].lower() + "). Confirm that this is a "
               "manual-only inspection to sign off.") if need == "manual_only" else (
               "The model returned no candidates, which is not proof that the surface is defect-free. Confirm that you examined the "
               "image yourself to sign off.")
        raise api_error(409, E.REVIEW_INCOMPLETE, msg, field="acknowledgement", required_acknowledgement=need, detection_state=det["state"])
    db.commit()

    def build():
        cur = db.get(Inspection, insp.id)
        rev = expected
        if cur.status in ("awaiting_review", "analysis_failed"):          # signing off with nothing recorded: enter review first
            rev = L.transition(db, insp.id, "in_review", expected_revision=rev)
        rev = L.transition(db, insp.id, "reviewed", expected_revision=rev,
                           fields={"signed_off_at": now(), "signed_off_by": user.id, "signoff_outcome": outcome, "signoff_note": note})
        ev = _event(insp, user, rev, target_type="inspection", kind="signoff", comment=note,
                    previous={"outcome": outcome, "counts": counts, "detection_state": det["state"], "acknowledgement": need,
                              "acknowledgement_text": ST.ACKS.get(need) if need else None})
        db.add(ev)
        db.flush()
        audit(db, "inspection.signed_off", "inspection", insp.id, user_id=user.id, ip=client_ip(request),
              meta={"event_id": ev.id, "revision": rev, "outcome": outcome, "counts": counts, "detection_state": det["state"], "acknowledgement": need})
        return ev, {}
    return _commit_event(db, request, user, insp, role, scope, rh, build)


def reopen(db: Session, request: Request, user: User, insp_id: str, body: dict):
    """EP-16: reviewed → in_review. The sign-off stays in history; existing reports become stale (derived)."""
    insp, role, expected = _guard(db, insp_id, user, request)
    scope = f"user:{user.id}:review:{insp.id}"
    rh = IDEM.request_hash(request, {"if_match": expected, "body": body, "op": "reopen"})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    _check_revision(insp, expected)
    if insp.status != "reviewed":
        raise api_error(409, E.CONFLICT, "Only a signed-off inspection can be reopened.", current_status=insp.status)
    comment = clean_text(body.get("comment"), COMMENT_MAX, "comment")
    db.commit()

    def build():
        prev = {"signed_off_at": SVC.iso(insp.signed_off_at), "signed_off_by": insp.signed_off_by, "outcome": insp.signoff_outcome}
        rev = L.transition(db, insp.id, "in_review", expected_revision=expected,
                           fields={"signed_off_at": None, "signed_off_by": None, "signoff_outcome": None, "signoff_note": None})
        ev = _event(insp, user, rev, target_type="inspection", kind="reopen", comment=comment, previous=prev)
        db.add(ev)
        db.flush()
        audit(db, "inspection.reopened", "inspection", insp.id, user_id=user.id, ip=client_ip(request), meta={"event_id": ev.id, "revision": rev, "previous": prev})
        return ev, {}
    return _commit_event(db, request, user, insp, role, scope, rh, build)


def history(db: Session, user: User, insp_id: str) -> dict:
    """EP-14: every review event, oldest first, with actor and the previous value. Read-only."""
    insp, _ = SVC.inspection_for_user(db, insp_id, user)
    events = db.scalars(select(FindingDecision).where(FindingDecision.inspection_id == insp.id).order_by(FindingDecision.inspection_revision)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({e.actor_id for e in events})))} if events else {}
    return {"items": [event_out(e, users) for e in events], "total": len(events), "current_revision": insp.revision}
