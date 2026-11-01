"""Inspection persistence (Phase 10 = Phase 5 release R2): create, read, list, update, soft delete, media access.

Rules kept here (docs/API_ARCHITECTURE.md, DATA_FLOW_AND_CONSISTENCY.md):
  · workspace scope: an inspection is visible only to members of its workspace; anything else is 404;
  · permissions come from the workspace role (permissions.WORKSPACE_ROLE_PERMISSIONS);
  · object keys are generated here and never leave the server; responses carry only /api/media URLs;
  · status changes only through lifecycle.py; metadata changes need the caller's revision (If-Match);
  · no operational detection model exists, so every inspection is analysis_mode='none'. Experimental runs
    (ADR-014) are linked for reference only and never change status, findings or counts of findings.
"""
from __future__ import annotations

import base64
import hashlib
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import and_, false, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .. import errors as E
from .. import permissions as P
from ..audit import audit
from ..db import SessionLocal
from ..errors import api_error
from ..logs import request_id_var
from ..models import (COMPONENTS, ExperimentalRun, Inspection, InspectionMedia, InspectionRefCounter, MediaRendition, Project, User, Workspace,
                      WorkspaceMember, now, uid)
from ..security import client_ip, personal_workspace, workspace_for_user, workspace_role
from ..services import idempotency as IDEM
from ..storage import new_key, storage
from . import lifecycle as L
from . import objects as OBJ
from .media import MediaRejected, ValidatedMedia
from .media import sanitize_filename
from .media import validate as validate_media

CATEGORIES = ("crack", "dent", "corrosion", "other")
SORTS = {"created_at": Inspection.created_at, "updated_at": Inspection.updated_at, "ref": Inspection.ref, "component": Inspection.component, "status": Inspection.status}
TEXT_LIMITS = {"zone": 60, "aircraft_ref": 60, "notes": 1000}
NOT_FOUND_MSG = "This inspection does not exist or is not visible to you."


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ====================================================================== write retries
def _busy(ex: OperationalError) -> bool:
    m = str(getattr(ex, "orig", ex)).lower()
    return "locked" in m or "busy" in m or "could not serialize" in m or "deadlock" in m


def write_retry(db: Session, fn, attempts: int = 8):
    """Run a write transaction (`fn` must commit). A transient lock/serialisation failure rolls back completely and the
    whole transaction is re-run; every write in it is conditional or freshly allocated, so a re-run is safe."""
    for i in range(attempts):
        try:
            return fn()
        except OperationalError as ex:
            db.rollback()
            if not _busy(ex) or i == attempts - 1:
                raise
            time.sleep(min(0.5, 0.01 * 2 ** i) + random.random() * 0.02)


# ====================================================================== authorisation
def _not_found():
    return api_error(404, E.INSPECTION_NOT_FOUND, NOT_FOUND_MSG)


def inspection_for_user(db: Session, insp_id: str, user: User, permission: str = "inspection.read") -> tuple[Inspection, str]:
    """The guard every inspection route calls. Unknown, deleted and other-workspace inspections are all 404."""
    insp = db.get(Inspection, insp_id) if insp_id and len(insp_id) <= 64 else None
    role = workspace_role(db, insp.workspace_id, user) if insp is not None else None
    if insp is None or insp.deleted_at is not None or role is None:
        raise _not_found()
    ws = db.get(Workspace, insp.workspace_id)
    if ws is None or ws.archived_at is not None:
        raise _not_found()
    if not P.workspace_can(role, permission):
        raise api_error(403, E.FORBIDDEN, "Your role in this workspace does not allow this action.", permission=permission)
    return insp, role


def member_workspace_ids(db: Session, user: User) -> list[str]:
    return list(db.scalars(select(WorkspaceMember.workspace_id).join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                           .where(WorkspaceMember.user_id == user.id, Workspace.archived_at.is_(None))))


def allowed_actions(role: str) -> list[str]:
    out = []
    for perm, action in (("inspection.create", "update"), ("inspection.delete", "delete"), ("media.read_original", "read_original")):
        if P.workspace_can(role, perm):
            out.append(action)
    return out


# ====================================================================== serialisation
def media_urls(m: InspectionMedia, role: str) -> dict:
    u = {"display": f"/api/media/inspection/{m.id}/display", "thumb": f"/api/media/inspection/{m.id}/thumb"}
    if P.workspace_can(role, "media.read_original"):
        u["original"] = f"/api/media/inspection/{m.id}/original"
    return u


def media_out(m: InspectionMedia, role: str) -> dict:
    return {"id": m.id, "ordinal": m.ordinal, "kind": m.kind, "filename": m.filename, "mime": m.mime, "size_bytes": m.size_bytes,
            "width": m.width, "height": m.height, "duration_ms": m.duration_ms, "sha256": m.sha256, "capture": m.capture or {"has_gps": False},
            "quality": m.quality or {}, "integrity": m.integrity, "integrity_checked_at": iso(m.integrity_checked_at),
            "retention_state": m.retention_state, "uploaded_at": iso(m.uploaded_at), "urls": media_urls(m, role)}


def _counts(media: int, exp_runs: int) -> dict:
    # Review persistence is Phase 11: no server-side findings or decisions exist yet, so these are genuinely zero.
    return {"media": media, "predictions": 0, "human_findings": 0, "pending": 0, "confirmed": 0, "rejected": 0, "further_review": 0,
            "experimental_runs": exp_runs}


def summary_out(i: Inspection, creator: User | None, ws: Workspace | None, role: str, media_count: int, exp_runs: int, thumb_media: str | None) -> dict:
    return {
        "id": i.id, "ref": i.ref, "component": i.component, "zone": i.zone, "aircraft_ref": i.aircraft_ref, "status": i.status,
        "analysis_mode": i.analysis_mode, "created_at": iso(i.created_at), "updated_at": iso(i.updated_at), "revision": i.revision,
        "created_by": {"id": creator.id, "name": creator.name} if creator else None,
        "workspace": {"id": ws.id, "name": ws.name} if ws else None,
        "counts": _counts(media_count, exp_runs), "categories": [], "signed_off_at": iso(i.signed_off_at), "has_report": False, "report_stale": None,
        "thumb_url": f"/api/media/inspection/{thumb_media}/thumb" if thumb_media else None, "origin": "server",
    }


def full_out(db: Session, i: Inspection, role: str, warnings: list | None = None) -> dict:
    media = db.scalars(select(InspectionMedia).where(InspectionMedia.inspection_id == i.id).order_by(InspectionMedia.ordinal)).all()
    runs = db.scalar(select(func.count()).select_from(ExperimentalRun).where(ExperimentalRun.inspection_id == i.id)) or 0
    out = summary_out(i, db.get(User, i.created_by), db.get(Workspace, i.workspace_id), role, len(media), runs, media[0].id if media else None)
    out.update({
        "notes": i.notes or "", "project_id": i.project_id, "signoff_note": i.signoff_note, "signoff_outcome": i.signoff_outcome, "signed_off_by": i.signed_off_by,
        "last_error_code": i.last_error_code, "media": [media_out(m, role) for m in media], "analysis": None, "predictions": [], "human_findings": [],
        "current_decisions": {}, "reports": [], "warnings": warnings if warnings is not None else duplicate_warnings(db, i, media),
        "allowed_actions": allowed_actions(role),
        "persistence": {"inspection": True, "review": False, "reports": False,
                        "note": "The inspection and its original image are stored on the server. Review decisions, findings and reports are not stored on the server yet."},
    })
    if i.analysis_mode == "none":
        out["analysis_note"] = "No detection model analysed this inspection. Findings can be recorded by a person, or the analysis can be run."
    from .review import enrich_full          # Phase 11: fill the review fields (same shape; empty when no review exists)
    enrich_full(db, i, role, out)
    return out


def duplicate_warnings(db: Session, i: Inspection, media: list[InspectionMedia]) -> list[dict]:
    """FR-VAL-03: the same image bytes in another live inspection of this workspace. A warning, never a block."""
    out = []
    for m in media:
        others = db.scalars(select(InspectionMedia.inspection_id).join(Inspection, Inspection.id == InspectionMedia.inspection_id)
                            .where(InspectionMedia.workspace_id == i.workspace_id, InspectionMedia.sha256 == m.sha256, InspectionMedia.inspection_id != i.id,
                                   Inspection.deleted_at.is_(None)).order_by(Inspection.created_at).limit(20)).all()
        if others:
            refs = dict(db.execute(select(Inspection.id, Inspection.ref).where(Inspection.id.in_(others))).all())
            out.append({"code": E.DUPLICATE_MEDIA, "media_id": m.id, "inspection_ids": list(others), "refs": [refs[x] for x in others if x in refs],
                        "message": "The same image (identical SHA-256) is already part of another inspection in this workspace. Both records are kept."})
    return out


# ====================================================================== create (EP-01)
def _text(form: dict, name: str) -> str | None:
    v = form.get(name)
    if v is None:
        return None
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(v)).strip()
    if len(v) > TEXT_LIMITS[name]:
        raise api_error(422, E.VALIDATION, f"{name.replace('_', ' ').capitalize()} must be at most {TEXT_LIMITS[name]} characters.", field=name)
    return v or None


def validate_fields(form: dict) -> dict:
    comp = (form.get("component") or "").strip()
    if comp not in COMPONENTS:
        raise api_error(422, E.VALIDATION, "Choose the aircraft component this image shows.", field="component", allowed=list(COMPONENTS))
    return {"component": comp, "zone": _text(form, "zone"), "aircraft_ref": _text(form, "aircraft_ref"), "notes": _text(form, "notes") or ""}


def resolve_workspace(db: Session, user: User, workspace_id: str | None) -> tuple[Workspace, str]:
    ws = workspace_for_user(db, workspace_id, user) if workspace_id else personal_workspace(db, user)
    role = workspace_role(db, ws.id, user)
    if not P.workspace_can(role, "inspection.create"):
        raise api_error(403, E.FORBIDDEN, "Your role in this workspace does not allow creating inspections.", permission="inspection.create")
    return ws, role


def next_ref(db: Session, workspace_id: str, year: int) -> str:
    """Allocate INS-YYYY-NNNNNN. The UPDATE is the first write of the transaction, so concurrent creators serialise on
    the counter row (row lock on PostgreSQL, the write lock on SQLite) and never get the same number."""
    for _ in range(5):
        n = db.execute(update(InspectionRefCounter).where(InspectionRefCounter.workspace_id == workspace_id, InspectionRefCounter.year == year)
                       .values(last_value=InspectionRefCounter.last_value + 1).execution_options(synchronize_session=False)).rowcount
        if n == 1:
            v = db.scalar(select(InspectionRefCounter.last_value).where(InspectionRefCounter.workspace_id == workspace_id, InspectionRefCounter.year == year))
            break
        try:
            with db.begin_nested():
                db.add(InspectionRefCounter(workspace_id=workspace_id, year=year, last_value=1))
            v = 1
            break
        except IntegrityError:
            continue
    else:  # pragma: no cover
        raise api_error(503, E.SERVICE_UNAVAILABLE, "Could not allocate an inspection reference. Try again.")
    if v > 999_999:
        raise api_error(409, E.CONFLICT, "This workspace has reached the yearly inspection reference limit.")
    return f"INS-{year}-{v:06d}"


def create(db: Session, request: Request, user: User, data: bytes, filename: str | None, form: dict) -> Response:
    """EP-01 as in API_ARCHITECTURE §5.1: idempotency → validation → TX1 intents → PUT ×3 → TX2 rows + commit intents."""
    from fastapi.responses import JSONResponse

    fields = validate_fields(form)
    ws, role = resolve_workspace(db, user, form.get("workspace_id") or None)
    project_id = (form.get("project_id") or "").strip() or None
    if project_id:
        prj = db.get(Project, project_id) if len(project_id) <= 64 else None
        if prj is None or prj.workspace_id != ws.id or prj.archived_at is not None:
            raise api_error(422, E.VALIDATION, "The project must belong to the same workspace.", field="project_id")
    analyse = str(form.get("analyse", "true")).lower() not in ("false", "0", "no")
    file_sha = hashlib.sha256(data).hexdigest()
    scope = f"user:{user.id}:inspections.create"
    rh = IDEM.request_hash(request, {"ws": ws.id, "project": project_id, "fields": fields, "file": file_sha, "analyse": analyse})
    rep = IDEM.replay(db, request, scope, rh)
    if rep is not None:
        return rep
    try:
        v: ValidatedMedia = validate_media(data)
    except MediaRejected as ex:
        audit(db, "inspection.create", "inspection", None, user_id=user.id, result="failure", meta={"code": ex.code, "reason": ex.reason, "bytes": len(data)}, ip=client_ip(request))
        db.commit()
        raise api_error(ex.status, ex.code, str(ex), **({"reason": ex.reason} if ex.reason else {})) from None

    rid = request_id_var.get()
    keys = {"original": new_key("inspections", v.ext), **{r.kind: new_key("inspection-renditions", "jpg") for r in v.renditions}}
    # TX1: durable write intents before any byte is written
    OBJ.register_pending(db, [(keys["original"], "media_original", v.mime)] + [(keys[r.kind], "media_rendition", r.mime) for r in v.renditions], rid)
    db.commit()
    written: list[str] = []
    try:
        storage.put(keys["original"], v.stored if v.stored is not None else data, v.mime)
        written.append(keys["original"])
        for r in v.renditions:
            storage.put(keys[r.kind], r.data, r.mime)
            written.append(keys[r.kind])
    except Exception:
        OBJ.compensate(SessionLocal, storage, written)
        audit(db, "inspection.create", "inspection", None, user_id=user.id, result="failure", meta={"code": E.STORAGE_UNAVAILABLE}, ip=client_ip(request))
        db.commit()
        raise api_error(503, E.STORAGE_UNAVAILABLE, "The image store is unavailable. Nothing was saved; try again.") from None

    # Phase 13: operational, review-assisted detection runs before TX2 so the inspection, its run and its predictions commit together
    from . import analysis as AN
    eng = result = failure = None
    no_model_reason = None
    requested_at = now()
    if analyse:
        eng, why = AN.engine()
        if eng is None:
            no_model_reason = why
        else:
            eng, result, failure = AN.analyse_bytes(data, sanitize_filename(filename or "image"))
    mode = "model" if eng is not None else "none"

    # TX2: reference number, rows, intents -> committed, audit, idempotency record — all or nothing
    def tx2():
        ts = now()
        path = L.creation_path(mode)
        if mode == "model":
            path.append("analysis_failed" if failure else "awaiting_review")
            assert L.allowed(path[-2], path[-1])
        insp = Inspection(id=uid(), workspace_id=ws.id, project_id=project_id, ref=next_ref(db, ws.id, ts.year), status=path[-1], analysis_mode=mode,
                          last_error_code=failure.code if failure else None,
                          revision=0, created_at=ts, updated_at=ts, created_by=user.id, **fields)
        db.add(insp)
        db.flush()
        m = InspectionMedia(id=uid(), workspace_id=ws.id, inspection_id=insp.id, ordinal=1, kind="image", filename=sanitize_filename(filename or "image"),
                            mime=v.mime, size_bytes=v.size_bytes, width=v.width, height=v.height, sha256=v.sha256, object_key=keys["original"],
                            capture=v.capture, quality=v.quality, source="upload", integrity="ok", integrity_checked_at=ts, uploaded_at=ts, uploaded_by=user.id)
        db.add(m)
        db.flush()
        OBJ.mark_committed(db, keys["original"], v.sha256, v.size_bytes, "inspection_media", m.id)
        for r in v.renditions:
            rr = MediaRendition(id=uid(), media_id=m.id, kind=r.kind, object_key=keys[r.kind], mime=r.mime, sha256=r.sha256, size_bytes=len(r.data),
                                width=r.width, height=r.height, created_at=ts)
            db.add(rr)
            db.flush()
            OBJ.mark_committed(db, keys[r.kind], r.sha256, len(r.data), "media_rendition", rr.id)
        warnings = duplicate_warnings(db, insp, [m])
        run = AN.persist(db, insp, m, user.id, eng, result, failure, requested_at) if mode == "model" else None
        audit(db, "inspection.create", "inspection", insp.id, user_id=user.id, result="success", ip=client_ip(request),
              meta={"ref": insp.ref, "workspace_id": ws.id, "status_path": path, "analysis_mode": mode, "analysis_requested": analyse,
                    "model_id": eng.model_id if eng else None, "analysis_run_id": run.id if run else None,
                    "analysis_error": failure.code if failure else None, "predictions": len(result.output["detections"]) if result else 0,
                    "out_of_domain": bool(result and result.out_of_domain),
                    "media_sha256": v.sha256, "bytes": v.size_bytes, "duplicate_of": [w["inspection_ids"] for w in warnings]})
        body = full_out(db, insp, role, warnings)
        if analyse and mode == "none":
            body["analysis_note"] = f"No detection model analysed this image: {no_model_reason} The inspection was saved for manual review."
        elif failure:
            body["analysis_note"] = f"Detection failed ({failure.code}): {failure}. The inspection was saved; run the analysis again."
        elif AN.out_of_domain_note(result):
            body["analysis_note"] = AN.out_of_domain_note(result)
        IDEM.claim(db, request, scope, 201, body, rh)
        db.commit()
        return insp, body

    try:
        insp, body = write_retry(db, tx2)
    except IntegrityError:
        db.rollback()
        OBJ.compensate(SessionLocal, storage, written)
        rep = IDEM.replay(db, request, scope, rh)   # a concurrent request with the same key won: return its answer
        if rep is not None:
            return rep
        raise
    except Exception:
        db.rollback()
        OBJ.compensate(SessionLocal, storage, written)
        raise
    return JSONResponse(body, status_code=201, headers={"ETag": f'"{insp.revision}"', "Location": f"/api/v1/inspections/{insp.id}"})


# ====================================================================== list (EP-07)
def _date(v: str | None, name: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise api_error(422, E.VALIDATION, f"'{name}' must be a date (YYYY-MM-DD).", field=name) from None


def list_inspections(db: Session, user: User, *, workspace_id: str | None, q: str | None, component: str | None, status: str | None, category: str | None,
                     date_from: str | None, date_to: str | None, mine: bool, sort: str, direction: str, page: int, per_page: int) -> dict:
    if component and component not in COMPONENTS:
        raise api_error(422, E.VALIDATION, "Unknown component.", field="component", allowed=list(COMPONENTS))
    if status and status not in L.STATES:
        raise api_error(422, E.VALIDATION, "Unknown status.", field="status", allowed=list(L.STATES))
    if category and category not in CATEGORIES:
        raise api_error(422, E.VALIDATION, "Unknown defect category.", field="category", allowed=list(CATEGORIES))
    if sort not in SORTS:
        raise api_error(422, E.VALIDATION, "Unknown sort field.", field="sort", allowed=list(SORTS))
    if direction not in ("asc", "desc"):
        raise api_error(422, E.VALIDATION, "dir must be asc or desc.", field="dir")
    if q is not None and len(q) > 100:
        raise api_error(422, E.VALIDATION, "Search text must be at most 100 characters.", field="q")
    d0, d1 = _date(date_from, "from"), _date(date_to, "to")
    if d0 and d1 and d0 > d1:
        raise api_error(422, E.VALIDATION, "'from' must not be after 'to'.", field="from")
    if workspace_id:
        ws_ids = [workspace_for_user(db, workspace_id, user).id]
    else:
        ws_ids = member_workspace_ids(db, user)
    roles = dict(db.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(WorkspaceMember.user_id == user.id)).all())
    ws_ids = [w for w in ws_ids if P.workspace_can(roles.get(w), "inspection.read")]
    cond = [Inspection.workspace_id.in_(ws_ids) if ws_ids else false(), Inspection.deleted_at.is_(None)]
    if component:
        cond.append(Inspection.component == component)
    if status:
        cond.append(Inspection.status == status)
    if mine:
        cond.append(Inspection.created_by == user.id)
    if d0:
        cond.append(Inspection.created_at >= datetime.combine(d0, datetime.min.time(), timezone.utc))
    if d1:
        cond.append(Inspection.created_at < datetime.combine(d1 + timedelta(days=1), datetime.min.time(), timezone.utc))
    if q and q.strip():
        pat = "%" + re.sub(r"([\\%_])", r"\\\1", q.strip().lower()) + "%"
        # Phase C: extracted document text (original OCR output and the separately confirmed correction) is searchable too;
        # the evidence row must belong to the same inspection and workspace, so the workspace filter above still bounds it
        from ..models import TextEvidence
        in_text = select(TextEvidence.id).where(TextEvidence.inspection_id == Inspection.id, TextEvidence.workspace_id == Inspection.workspace_id,
                                                or_(func.lower(TextEvidence.original_text).like(pat, escape="\\"),
                                                    func.lower(TextEvidence.corrected_text).like(pat, escape="\\"))).exists()
        cond.append(or_(*(func.lower(c).like(pat, escape="\\") for c in (Inspection.ref, Inspection.zone, Inspection.aircraft_ref, Inspection.notes)), in_text))
    notes = []
    if category:
        # category = a CONFIRMED finding of that class (reviewer finding, or AI prediction confirmed/reclassified to it) - Phase 11
        from .review import category_inspection_ids
        ids = category_inspection_ids(db, category, ws_ids)
        cond.append(Inspection.id.in_(ids) if ids else false())
    where = and_(*cond)
    total = db.scalar(select(func.count()).select_from(Inspection).where(where)) or 0
    col = SORTS[sort]
    order = [col.asc(), Inspection.id.asc()] if direction == "asc" else [col.desc(), Inspection.id.desc()]
    rows = db.scalars(select(Inspection).where(where).order_by(*order).offset((page - 1) * per_page).limit(per_page)).all()
    ids = [r.id for r in rows]
    mc = dict(db.execute(select(InspectionMedia.inspection_id, func.count()).where(InspectionMedia.inspection_id.in_(ids)).group_by(InspectionMedia.inspection_id)).all()) if ids else {}
    first = dict(db.execute(select(InspectionMedia.inspection_id, InspectionMedia.id).where(InspectionMedia.inspection_id.in_(ids), InspectionMedia.ordinal == 1)).all()) if ids else {}
    ec = dict(db.execute(select(ExperimentalRun.inspection_id, func.count()).where(ExperimentalRun.inspection_id.in_(ids)).group_by(ExperimentalRun.inspection_id)).all()) if ids else {}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.created_by for r in rows})))} if rows else {}
    wss = {w.id: w for w in db.scalars(select(Workspace).where(Workspace.id.in_({r.workspace_id for r in rows})))} if rows else {}
    items = [summary_out(r, users.get(r.created_by), wss.get(r.workspace_id), roles.get(r.workspace_id), mc.get(r.id, 0), ec.get(r.id, 0), first.get(r.id)) for r in rows]
    from .review import enrich_summaries     # Phase 11: review counts, confirmed categories, report flags
    enrich_summaries(db, list(rows), items)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": max(1, -(-total // per_page)),
            "sort": sort, "dir": direction, "notes": notes}


# ====================================================================== update / delete (EP-09, EP-10)
def parse_if_match(request: Request) -> int:
    raw = (request.headers.get("if-match") or "").strip()
    if not raw:
        raise api_error(428, E.REVISION_REQUIRED, "Send the inspection's revision in If-Match (reload the inspection to get it).")
    raw = raw[2:] if raw.startswith("W/") else raw
    raw = raw.strip('"')
    if not raw.isdigit() or len(raw) > 9:
        raise api_error(422, E.VALIDATION, "If-Match must be the inspection revision (an integer).", field="If-Match")
    return int(raw)


def patch(db: Session, request: Request, user: User, insp_id: str, body: dict) -> dict:
    insp, role = inspection_for_user(db, insp_id, user, "inspection.create")
    expected = parse_if_match(request)
    allowed = {"component", "zone", "aircraft_ref", "notes"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise api_error(422, E.VALIDATION, f"These fields cannot be changed here: {', '.join(unknown)}.", field=unknown[0])
    if not body:
        raise api_error(422, E.VALIDATION, "Nothing to change.")
    fields: dict[str, Any] = {}
    if "component" in body:
        if body["component"] not in COMPONENTS:
            raise api_error(422, E.VALIDATION, "Unknown component.", field="component", allowed=list(COMPONENTS))
        fields["component"] = body["component"]
    for k in ("zone", "aircraft_ref", "notes"):
        if k in body:
            if body[k] is not None and not isinstance(body[k], str):
                raise api_error(422, E.VALIDATION, f"{k} must be text.", field=k)
            v = _text({k: body[k] or ""}, k)
            fields[k] = (v or "") if k == "notes" else v
    before = {k: getattr(insp, k) for k in fields}
    db.commit()     # end the read snapshot first: the conditional UPDATE then starts a fresh write transaction (SQLite WAL)

    def tx():
        new_rev = L.update_fields(db, insp.id, expected, fields)
        audit(db, "inspection.update", "inspection", insp.id, user_id=user.id, ip=client_ip(request),
              meta={"revision": new_rev, "fields": sorted(fields), "changed": sorted(k for k in fields if before[k] != fields[k])})
        db.commit()
    write_retry(db, tx)
    db.refresh(insp)
    return full_out(db, insp, role)


def soft_delete(db: Session, request: Request, user: User, insp_id: str) -> None:
    """EP-10: naturally idempotent soft delete. Media objects are kept (retention decides purging, docs/PHASE_10_RECOVERY.md)."""
    insp = db.get(Inspection, insp_id) if insp_id and len(insp_id) <= 64 else None
    if insp is not None and insp.deleted_at is not None and workspace_role(db, insp.workspace_id, user) is not None:
        return                                  # already deleted: same outcome, nothing to do
    insp, _ = inspection_for_user(db, insp_id, user, "inspection.delete")
    if insp.status == "analysing":
        raise api_error(409, E.ANALYSIS_IN_PROGRESS, "An analysis is running; wait for it to finish before deleting.")
    db.commit()     # end the read snapshot before writing (see patch)

    def tx():
        n = db.execute(update(Inspection).where(Inspection.id == insp.id, Inspection.deleted_at.is_(None))
                       .values(deleted_at=now(), deleted_by=user.id, updated_at=now()).execution_options(synchronize_session=False)).rowcount
        if n:
            audit(db, "inspection.delete", "inspection", insp.id, user_id=user.id, ip=client_ip(request), meta={"ref": insp.ref, "soft": True})
        db.commit()
    write_retry(db, tx)


# ====================================================================== media (EP-11, kind=inspection)
MEDIA_HEADERS = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
                 "Cross-Origin-Resource-Policy": "same-origin"}


def media_response(db: Session, request: Request, user: User, media_id: str, variant: str) -> Response:
    if variant not in ("display", "thumb", "original"):
        raise api_error(404, E.NOT_FOUND, "Not found.")
    m = db.get(InspectionMedia, media_id) if media_id and len(media_id) <= 64 else None
    if m is None:
        raise api_error(404, E.NOT_FOUND, "Not found.")
    try:
        insp, role = inspection_for_user(db, m.inspection_id, user, "media.read_original" if variant == "original" else "media.read")
    except Exception as ex:
        if getattr(ex, "status_code", None) == 404:
            raise api_error(404, E.NOT_FOUND, "Not found.") from None
        raise
    if m.retention_state == "purged":
        raise api_error(404, E.NOT_FOUND, "This image was removed under the retention policy.")
    if variant == "original":
        key, mime, sha, size = m.object_key, m.mime, m.sha256, m.size_bytes
    else:
        r = db.scalar(select(MediaRendition).where(MediaRendition.media_id == m.id, MediaRendition.kind == variant))
        if r is None:
            raise api_error(404, E.NOT_FOUND, "Not found.")
        key, mime, sha, size = r.object_key, r.mime, r.sha256, r.size_bytes
    try:
        data = storage.get(key)
    except FileNotFoundError:
        data = None
    except Exception:
        raise api_error(503, E.STORAGE_UNAVAILABLE, "The image store is unavailable. Try again later.") from None
    actual = hashlib.sha256(data).hexdigest() if data is not None else None
    if actual != sha:
        problem = "missing" if data is None else "checksum_mismatch"
        db.execute(update(InspectionMedia).where(InspectionMedia.id == m.id).values(integrity=problem, integrity_checked_at=now()))
        audit(db, "media.integrity_failed", "inspection_media", m.id, user_id=user.id, result="failure", ip=client_ip(request),
              meta={"inspection_id": insp.id, "variant": variant, "problem": problem})
        db.commit()
        raise api_error(409, E.INTEGRITY_FAILED, "A stored image failed its integrity check and was not served.", reason=problem, variant=variant)
    if variant == "original":
        if m.integrity != "ok":
            db.execute(update(InspectionMedia).where(InspectionMedia.id == m.id).values(integrity="ok", integrity_checked_at=now()))
            db.commit()
        audit(db, "media.read_original", "inspection_media", m.id, user_id=user.id, ip=client_ip(request), meta={"inspection_id": insp.id})
        db.commit()
    headers = dict(MEDIA_HEADERS)
    headers["Digest"] = "sha-256=" + base64.b64encode(bytes.fromhex(sha)).decode()
    headers["ETag"] = f'"{sha[:32]}"'
    if variant == "original":
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", m.filename)[:120] or "image"
        headers["Content-Disposition"] = f'attachment; filename="{insp.ref}-{safe}"'
    return Response(data, media_type=mime, headers=headers)
