"""Server reports (Phase 11; ADR-011, DATA_FLOW_AND_CONSISTENCY stage 10-11).

Rules
  · Only a signed-off (status 'reviewed') inspection is reported; the report pins inspection_revision, the analysis run and
    model version (NULL when no model ran) and the media hashes re-verified at generation.
  · One live report per (inspection, revision, template): a repeated request returns it (natural idempotency). A later
    reopen + sign-off is a new revision and therefore a NEW report version; older versions stay retrievable and are
    marked stale. An available report is frozen (ORM + database triggers); report rows are never deleted.
  · Lifecycle used: generating -> available | failed. ('queued' exists in the schema for a future worker; generation is
    synchronous here and takes well under a second.) A 'generating' row older than STALE_GENERATING is an interrupted
    attempt and is marked failed (WORKER_LOST) before a new attempt.
  · Bytes follow the Phase 10 storage protocol: intent (pending) -> PUT -> metadata + intent committed in one transaction;
    on failure the object is removed and the intent closed. The PDF's SHA-256 is stored and re-checked on every download.
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import errors as E
from ..audit import audit
from ..db import SessionLocal
from ..errors import api_error
from ..inspection import objects as OBJ
from ..inspection import review as RV
from ..inspection import service as SVC
from ..logs import request_id_var
from ..models import (COMPONENTS, ExperimentalRun, Inspection, InspectionMedia, InspectionReport, MediaRendition, ModelVersion, StorageObject, TextEvidence, User, Workspace,
                      now, uid)
from ..security import client_ip
from ..storage import new_key, storage
from . import render as R

TEMPLATE = "VT-RPT-2"  # OCR appendix; frozen reports from the previous template remain available.
STALE_GENERATING = timedelta(minutes=10)
COMPONENT_LABEL = {"fuselage": "Fuselage", "wing_upper": "Wing - upper surface", "wing_lower": "Wing - lower surface", "empennage": "Empennage / tail",
                   "nacelle": "Engine nacelle / cowling", "control_surface": "Control surface", "landing_gear": "Landing-gear bay", "other": "Other"}
assert set(COMPONENT_LABEL) == set(COMPONENTS)
OUTCOME_LABEL = {"findings_recorded": "Findings recorded", "no_confirmed_findings": "No confirmed findings (this is not a statement that the surface is defect-free)"}
LIMITATIONS = [
    "Review decisions and reviewer findings are human judgements recorded in VisionTra; they are only as complete as the images and the review.",
    "AI confidence is an uncalibrated model score for its own box, not a measured probability that a defect is present. It is not severity, repair priority or airworthiness risk; VisionTra does not grade defects.",
    "Boxes are approximate image regions. They do not measure physical dimensions or define the extent of a structural defect.",
    "A missing prediction, or a rejected one, does not mean the area is free of defects.",
    "AI predictions come from the detection model named in section 2. Its readiness is EVALUATED_LIMITED: it was evaluated once on a held-out split of "
    "public datasets, not on this operator's fleet, and it misses defects and raises false alarms. Every AI prediction in this report carries a human decision.",
    "Experimental (development-only) model runs are never part of this report.",
]


def _iso(dt) -> str | None:
    return SVC.iso(dt)


def _when(dt) -> str:
    """Human-readable UTC time to the second, e.g. 2026-10-13 12:31:47 UTC."""
    return (SVC.iso(dt) or "")[:19].replace("T", " ") + " UTC" if dt else "Not recorded"


def _when_iso(s: str | None) -> str | None:
    return (s[:19].replace("T", " ") + " UTC") if s else None


def version_numbers(db: Session, insp_id: str) -> dict[str, int]:
    rows = db.execute(select(InspectionReport.id).where(InspectionReport.inspection_id == insp_id, InspectionReport.status == "available")
                      .order_by(InspectionReport.generated_at, InspectionReport.id)).scalars().all()
    return {rid: n for n, rid in enumerate(rows, 1)}


def report_out(r: InspectionReport, insp: Inspection, versions: dict[str, int], users: dict | None = None) -> dict:
    u = (users or {}).get(r.requested_by)
    out = {"id": r.id, "inspection_id": r.inspection_id, "inspection_ref": insp.ref, "inspection_revision": r.inspection_revision, "template_version": r.template_version,
           "status": r.status, "version": versions.get(r.id), "generated_at": _iso(r.generated_at), "requested_at": _iso(r.requested_at),
           "requested_by": {"id": r.requested_by, "name": u.name if u else None} if r.requested_by else None,
           "sha256": r.sha256, "size_bytes": r.size_bytes, "pages": r.pages, "error_code": r.error_code,
           "analysis_run_id": r.analysis_run_id, "model_version_id": r.model_version_id, "media_sha256": r.media_sha256,
           "stale": r.status == "available" and insp.revision > r.inspection_revision}
    if r.status == "available" and r.purged_at is None:
        out["download_url"] = f"/api/v1/reports/{r.id}/download"
    return out


def summaries(db: Session, insp: Inspection) -> list[dict]:
    rows = db.scalars(select(InspectionReport).where(InspectionReport.inspection_id == insp.id).order_by(InspectionReport.requested_at.desc(), InspectionReport.id)).all()
    if not rows:
        return []
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.requested_by for r in rows})))}
    v = version_numbers(db, insp.id)
    return [report_out(r, insp, v, users) for r in rows]


def _report_for_user(db: Session, report_id: str, user: User, permission: str) -> tuple[InspectionReport, Inspection]:
    r = db.get(InspectionReport, report_id) if report_id and len(report_id) <= 64 else None
    if r is None:
        raise api_error(404, E.NOT_FOUND, "Report not found.")
    try:
        insp, _ = SVC.inspection_for_user(db, r.inspection_id, user, permission)
    except Exception as ex:                       # never reveal that a report exists in a workspace the user cannot see
        if getattr(ex, "status_code", None) == 404:
            raise api_error(404, E.NOT_FOUND, "Report not found.") from None
        raise
    return r, insp


# ====================================================================== snapshot (everything the PDF shows, read for ONE revision)
def snapshot(db: Session, insp: Inspection, report: InspectionReport, version: int) -> dict:
    rev = report.inspection_revision
    st = RV.state(db, insp, up_to_revision=rev)
    media = db.scalars(select(InspectionMedia).where(InspectionMedia.inspection_id == insp.id).order_by(InspectionMedia.ordinal)).all()
    users = dict(st["users"])
    for uid_ in {m.uploaded_by for m in media} | {insp.created_by} | ({insp.signed_off_by} if insp.signed_off_by else set()):
        if uid_ not in users:
            users[uid_] = db.get(User, uid_)
    name = lambda i: users[i].name if i in users and users[i] else None  # noqa: E731
    mout = []
    for m in media:
        rend = db.scalar(select(MediaRendition).where(MediaRendition.media_id == m.id, MediaRendition.kind == "display"))
        data = storage.get(rend.object_key)
        if hashlib.sha256(data).hexdigest() != rend.sha256:
            raise _Integrity(m.id, "rendition_checksum_mismatch")
        mout.append({"id": m.id, "filename": m.filename, "mime": m.mime, "width": m.width, "height": m.height, "size_bytes": m.size_bytes, "sha256": m.sha256,
                     "capture": m.capture, "uploaded_at": _when(m.uploaded_at), "uploaded_by": name(m.uploaded_by),
                     "display_jpeg": data, "display_w": rend.width, "display_h": rend.height})
    run = st["run"]
    mv = db.get(ModelVersion, run.model_version_id) if run else None
    cur = st["current"]
    findings, per_media = [], {}
    n = 0
    preds_out = []
    tiers = _display_tiers(mv)
    for p in st["predictions"]:
        n += 1
        c = cur.get(f"prediction:{p.id}", {})
        m = next(x for x in media if x.id == p.media_id)
        loc = f"x {round(p.box_x * m.width)}, y {round(p.box_y * m.height)}, {round(p.box_w * m.width)} x {round(p.box_h * m.height)}"
        d = {"n": n, "id": p.id, "class_id": p.class_id, "score": p.score, "low_confidence": p.low_confidence, "location": loc, "decision": c.get("decision") or "pending",
             "corrected_class_id": c.get("corrected_class_id"), "tier": _tier(tiers, p.class_id, p.score)}
        preds_out.append(d)
        f = {"n": n, "category": c.get("corrected_class_id") or p.class_id, "ai_class": p.class_id, "source": "AI prediction", "location": loc, "decision": d["decision"],
             "decided_by": (c.get("actor") or {}).get("name"), "decided_at": _when_iso(c.get("at")),
             "comment": c.get("comment"), "box": {"x": p.box_x, "y": p.box_y, "w": p.box_w, "h": p.box_h}}
        findings.append(f)
        per_media.setdefault(p.media_id, []).append(f)
    for h in st["human_findings"]:
        n += 1
        c = cur.get(f"human_finding:{h.id}", {})
        m = next(x for x in media if x.id == h.media_id)
        f = {"n": n, "category": h.category, "source": "Reviewer", "decision": c.get("decision") or "confirmed",
             "location": f"x {round(h.box_x * m.width)}, y {round(h.box_y * m.height)}, {round(h.box_w * m.width)} x {round(h.box_h * m.height)}",
             "decided_by": (c.get("actor") or {}).get("name"), "decided_at": _when_iso(c.get("at")),
             "comment": c.get("comment"), "box": {"x": h.box_x, "y": h.box_y, "w": h.box_w, "h": h.box_h}}
        findings.append(f)
        per_media.setdefault(h.media_id, []).append(f)
    tnum = {f"prediction:{p['id']}": p["n"] for p in preds_out} | {f"human_finding:{h.id}": n0 for n0, h in enumerate(st["human_findings"], len(preds_out) + 1)}
    events = []
    for e in st["events"]:
        tid = e.prediction_id or e.human_finding_id
        key = f"{e.target_type}:{tid}"
        target = "Inspection" if e.target_type == "inspection" else (f"#{tnum[key]}" if key in tnum else f"deleted finding {tid[:8]}")
        result = (R.DEC_LABEL.get(e.decision, "") + (f" -> {R.CAT_LABEL.get(e.corrected_class_id)}" if e.corrected_class_id else "")) if e.decision else \
            ((e.previous or {}).get("outcome", "") if e.kind == "signoff" else "")
        events.append({"revision": e.inspection_revision, "at": _when(e.at), "actor": name(e.actor_id), "kind": e.kind, "target": target,
                       "result": OUTCOME_LABEL.get(result, result).split(" (")[0], "comment": e.comment})
    signoff_ev = next((e for e in reversed(st["events"]) if e.kind == "signoff"), None)
    if signoff_ev is None or signoff_ev.inspection_revision != rev:
        raise _Inconsistent("the pinned revision is not a sign-off")
    outcome = (signoff_ev.previous or {}).get("outcome") or insp.signoff_outcome
    ws = db.get(Workspace, insp.workspace_id)
    return {
        "report": {"id": report.id, "version": version, "template": TEMPLATE, "generated_at": _when(now()), "inspection_revision": rev},
        "inspection": {"id": insp.id, "ref": insp.ref, "component_label": COMPONENT_LABEL[insp.component], "zone": insp.zone, "aircraft_ref": insp.aircraft_ref,
                       "notes": insp.notes, "created_at": _when(insp.created_at), "created_by": name(insp.created_by), "workspace": ws.name if ws else None},
        "media": mout,
        "analysis": ({"model_label": (f"{(run.timings or {}).get('model_id')} · {mv.architecture}" if (run.timings or {}).get("model_id") else f"{mv.architecture} v{mv.version}") if mv else "unknown", "model_version_id": run.model_version_id, "run_id": run.id, "attempt": run.attempt,
                      "finished_at": _when(run.finished_at), "score_threshold": run.score_threshold, "iou_threshold": run.iou_threshold,
                      "review_threshold": run.review_threshold, "taxonomy_version": run.taxonomy_version} if run else None),
        "predictions": preds_out, "findings": findings, "findings_for_media": per_media, "deleted_findings": len(st["deleted_findings"]),
        "signoff": {"at": _when(signoff_ev.at), "by": name(signoff_ev.actor_id), "outcome": outcome, "outcome_label": OUTCOME_LABEL.get(outcome, nr_(outcome)),
                    "note": signoff_ev.comment, "acknowledgement": (signoff_ev.previous or {}).get("acknowledgement_text")},
        "detection": _detection_snapshot(db, insp, len(preds_out), run),
        "events": events,
        "text_evidence": _text_snapshot(db, insp),
        "experimental_runs": db.scalar(select(func.count()).select_from(ExperimentalRun).where(ExperimentalRun.inspection_id == insp.id)) or 0,
        "limitations": LIMITATIONS,
    }


def _text_snapshot(db, insp) -> list[dict]:
    # Report access is inspection/workspace scoped, as is evidence retrieval.
    # Sign-off prevents evidence edits; generate() also rechecks inspection revision.
    from ..inspection.text_evidence import out
    rows = db.scalars(select(TextEvidence).where(TextEvidence.inspection_id == insp.id,
                      TextEvidence.workspace_id == insp.workspace_id)
                      .order_by(TextEvidence.created_at, TextEvidence.id)).all()
    for row in rows:
        try:
            data = storage.get(row.object_key)
        except FileNotFoundError:
            raise _Integrity(row.id, "text_source_missing") from None
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise _Integrity(row.id, "text_source_checksum_mismatch")
    return [out(row) for row in rows]


def _detection_snapshot(db, insp, n_preds, run) -> dict:
    from ..inspection import states as ST
    d = ST.detection(db, insp, n_preds, run)
    g = d.get("gate") or {}
    return {"state": d["state"], "label": d["label"], "overridden": d["overridden"], "override_reason": g.get("override_reason"),
            "gate_reason": g.get("reason"), "gate_p": g.get("p_aircraft_surface"), "gate_id": g.get("gate_id")}


def nr_(v):
    return v if v else "Not recorded"


class _Integrity(Exception):
    def __init__(self, media_id: str, problem: str):
        super().__init__(problem)
        self.media_id, self.problem = media_id, problem


class _Inconsistent(Exception):
    pass


# ====================================================================== generation (EP-17)
def _fail(db: Session, report_id: str, code: str) -> None:
    db.rollback()
    db.execute(update(InspectionReport).where(InspectionReport.id == report_id, InspectionReport.status == "generating").values(status="failed", error_code=code))
    db.commit()


def generate(db: Session, request: Request, user: User, insp_id: str):
    insp, role = SVC.inspection_for_user(db, insp_id, user, "report.create")
    if insp.status != "reviewed":
        raise api_error(409, E.REPORT_NOT_READY, "A server report needs a signed-off inspection. Complete the review first.", reason="not_signed_off", current_status=insp.status)
    rev = insp.revision
    existing = db.scalars(select(InspectionReport).where(InspectionReport.inspection_id == insp.id, InspectionReport.inspection_revision == rev,
                                                       InspectionReport.template_version == TEMPLATE, InspectionReport.status.in_(("queued", "generating", "available")))).first()
    if existing is not None and existing.status == "available":
        return JSONResponse(report_out(existing, insp, version_numbers(db, insp.id)), status_code=200, headers={"X-Report-Existing": "true"})
    if existing is not None:
        started = existing.requested_at if existing.requested_at.tzinfo else existing.requested_at.replace(tzinfo=now().tzinfo)
        if now() - started < STALE_GENERATING:
            raise api_error(409, E.REPORT_NOT_READY, "This report is already being generated. Try again in a moment.", reason="generating", report_id=existing.id)
        db.execute(update(InspectionReport).where(InspectionReport.id == existing.id, InspectionReport.status.in_(("queued", "generating")))
                   .values(status="failed", error_code="WORKER_LOST"))       # interrupted attempt: close it, then start a new one
        db.commit()
    rep = InspectionReport(id=uid(), inspection_id=insp.id, inspection_revision=rev, template_version=TEMPLATE, status="generating", requested_by=user.id,
                           requested_at=now(), media_sha256=[])
    db.add(rep)
    try:
        db.commit()
    except IntegrityError:                         # a concurrent request created the live report for this revision first
        db.rollback()
        other = db.scalars(select(InspectionReport).where(InspectionReport.inspection_id == insp.id, InspectionReport.inspection_revision == rev,
                                                        InspectionReport.template_version == TEMPLATE, InspectionReport.status.in_(("generating", "available")))).first()
        if other is not None and other.status == "available":
            return JSONResponse(report_out(other, insp, version_numbers(db, insp.id)), status_code=200, headers={"X-Report-Existing": "true"})
        raise api_error(409, E.REPORT_NOT_READY, "This report is already being generated. Try again in a moment.", reason="generating") from None

    # 1. re-verify every original (ADR-011); a mismatch blocks the report and flags the media
    media = db.scalars(select(InspectionMedia).where(InspectionMedia.inspection_id == insp.id).order_by(InspectionMedia.ordinal)).all()
    hashes = []
    for m in media:
        try:
            data = storage.get(m.object_key)
        except FileNotFoundError:
            data = None
        except Exception:
            _fail(db, rep.id, E.STORAGE_UNAVAILABLE)
            raise api_error(503, E.STORAGE_UNAVAILABLE, "The image store is unavailable. No report was produced; try again.", report_id=rep.id) from None
        if data is None or hashlib.sha256(data).hexdigest() != m.sha256:
            problem = "missing" if data is None else "checksum_mismatch"
            _fail(db, rep.id, E.INTEGRITY_FAILED)
            db.execute(update(InspectionMedia).where(InspectionMedia.id == m.id).values(integrity=problem, integrity_checked_at=now()))
            audit(db, "media.integrity_failed", "inspection_media", m.id, user_id=user.id, result="failure", ip=client_ip(request),
                  meta={"inspection_id": insp.id, "during": "report_generation", "report_id": rep.id, "problem": problem})
            db.commit()
            raise api_error(409, E.INTEGRITY_FAILED, "A stored original failed its integrity check. No report was produced.", reason=problem, report_id=rep.id)
        hashes.append({"media_id": m.id, "sha256": m.sha256})

    # 2. snapshot + render (pure); 3. store; 4. commit metadata + intent together
    key = new_key("inspection-reports", "pdf")
    written = False
    try:
        version = len(version_numbers(db, insp.id)) + 1
        snap = snapshot(db, insp, rep, version)
        pdf, pages = R.render(snap)
        if not pdf.startswith(b"%PDF-1.4") or not pdf.rstrip().endswith(b"%%EOF"):
            raise RuntimeError("renderer produced an incomplete PDF")
        sha = hashlib.sha256(pdf).hexdigest()
        db.commit()                                   # end the read snapshot before the write transaction
        OBJ.register_pending(db, [(key, "report", "application/pdf")], request_id_var.get())
        db.commit()
        storage.put(key, pdf, "application/pdf")
        written = True
        cur = db.get(Inspection, insp.id)
        db.refresh(cur)
        if cur.revision != rev or cur.deleted_at is not None:
            raise _Inconsistent("the inspection changed while the report was generated")
        n = db.execute(update(InspectionReport).where(InspectionReport.id == rep.id, InspectionReport.status == "generating")
                       .values(status="available", object_key=key, sha256=sha, size_bytes=len(pdf), pages=pages, generated_at=now(), media_sha256=hashes,
                               analysis_run_id=snap["analysis"]["run_id"] if snap["analysis"] else None,
                               model_version_id=snap["analysis"]["model_version_id"] if snap["analysis"] else None)).rowcount
        if n != 1:
            raise _Inconsistent("the report row was closed by another request")
        OBJ.mark_committed(db, key, sha, len(pdf), "inspection_report", rep.id)
        audit(db, "report.generated", "inspection_report", rep.id, user_id=user.id, ip=client_ip(request),
              meta={"inspection_id": insp.id, "inspection_revision": rev, "version": version, "sha256": sha, "pages": pages, "template": TEMPLATE})
        db.commit()
    except _Integrity as ex:
        _fail(db, rep.id, E.INTEGRITY_FAILED)
        raise api_error(409, E.INTEGRITY_FAILED, "A stored image failed its integrity check. No report was produced.", reason=ex.problem, report_id=rep.id) from None
    except _Inconsistent as ex:
        if written:
            OBJ.compensate(SessionLocal, storage, [key])
        _fail(db, rep.id, E.REVIEW_CONFLICT)
        raise api_error(409, E.REVIEW_CONFLICT, f"No report was produced: {ex}. Reload and try again.", report_id=rep.id) from None
    except Exception:
        if written:
            OBJ.compensate(SessionLocal, storage, [key])
        else:
            db.rollback()
            db.execute(update(StorageObject).where(StorageObject.object_key == key, StorageObject.state == "pending").values(state="deleted", deleted_at=now()))
            db.commit()
        _fail(db, rep.id, E.REPORT_GENERATION_FAILED)
        audit(db, "report.generated", "inspection_report", rep.id, user_id=user.id, result="failure", ip=client_ip(request),
              meta={"inspection_id": insp.id, "inspection_revision": rev, "code": E.REPORT_GENERATION_FAILED})
        db.commit()
        raise api_error(500, E.REPORT_GENERATION_FAILED, "The report could not be produced. Nothing was stored; try again.", report_id=rep.id) from None
    db.refresh(rep)
    return JSONResponse(report_out(rep, insp, version_numbers(db, insp.id)), status_code=201)


# ====================================================================== retrieval (EP-18, EP-19)
def get(db: Session, user: User, report_id: str) -> dict:
    r, insp = _report_for_user(db, report_id, user, "report.read")
    return report_out(r, insp, version_numbers(db, insp.id), {u.id: u for u in [db.get(User, r.requested_by)] if u})


def download(db: Session, request: Request, user: User, report_id: str) -> Response:
    r, insp = _report_for_user(db, report_id, user, "report.read")
    if r.status != "available":
        raise api_error(409, E.REPORT_NOT_READY, "This report is not available.", reason=r.status, error_code=r.error_code)
    if r.purged_at is not None:
        raise api_error(404, E.NOT_FOUND, "This report file was removed under the retention policy; its record remains.")
    try:
        data = storage.get(r.object_key)
    except FileNotFoundError:
        data = None
    except Exception:
        raise api_error(503, E.STORAGE_UNAVAILABLE, "The report store is unavailable. Try again later.") from None
    if data is None or hashlib.sha256(data).hexdigest() != r.sha256:
        problem = "missing" if data is None else "checksum_mismatch"
        audit(db, "report.integrity_failed", "inspection_report", r.id, user_id=user.id, result="failure", ip=client_ip(request), meta={"problem": problem})
        db.commit()
        raise api_error(409, E.INTEGRITY_FAILED, "The stored report failed its integrity check and was not served.", reason=problem)
    v = version_numbers(db, insp.id).get(r.id)
    audit(db, "report.downloaded", "inspection_report", r.id, user_id=user.id, ip=client_ip(request), meta={"inspection_id": insp.id, "version": v})
    db.commit()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", insp.ref)
    return Response(data, media_type="application/pdf", headers={
        **SVC.MEDIA_HEADERS, "Digest": "sha-256=" + base64.b64encode(bytes.fromhex(r.sha256)).decode(), "ETag": f'"{r.sha256[:32]}"',
        "Content-Disposition": f'attachment; filename="VisionTra_{safe}_R{v}.pdf"'})


def _display_tiers(mv) -> dict | None:
    """Phase 19: validation-measured tiers of the model that produced the predictions (None for other models)."""
    import json
    from ..config import settings
    mid = ((mv.val_metrics or {}).get("model_id") if mv is not None else None)
    if not mid:
        return None
    try:
        d = json.loads((settings.detection_models_dir / mid / "display_tiers.json").read_text())
    except (OSError, ValueError):
        return None
    return d if d.get("model_id") == mid else None


def _tier(tiers: dict | None, cls: str, score: float) -> str | None:
    bands = (((tiers or {}).get("classes") or {}).get(cls) or {}).get("bands") or []
    for b in bands:
        if score >= b["from"] - 1e-9 and (b["to"] is None or score < b["to"] - 1e-9):
            return f"{b['tier']}, {round(b['hit_rate'] * 100)}% real" if b.get("hit_rate") is not None else b["tier"]
    return bands[-1]["tier"] if bands else None
