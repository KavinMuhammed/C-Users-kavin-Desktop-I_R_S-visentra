"""Workspaces and members, audit trail, health, version, capabilities, compute and authorised inspection media.

Phase 13: replaces the platform/insights routers of the former image-classification workbench. Health and
capability answers are measured when requested (a DB round-trip, a storage write/read, the detection model's
metadata, an import of the ML runtime); nothing is reported as working on the strength of configuration alone.
"""
from __future__ import annotations

import csv
import io
import os
import platform as _platform
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .. import errors as E
from .. import permissions as P
from ..audit import audit
from ..config import settings
from ..db import SessionLocal, get_db
from ..errors import api_error
from ..inspection import media as M
from ..models import AuditEvent, Inspection, User, Workspace, WorkspaceMember
from ..security import current_user, workspace_for_user, workspace_role
from ..storage import storage

router = APIRouter(prefix="/api", tags=["system"])
API_VERSION = "2.0.0"
STARTED = datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ====================================================================== workspaces
def _ws_out(db: Session, ws: Workspace, role: str | None) -> dict:
    members = db.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)) or 0
    inspections = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.workspace_id == ws.id, Inspection.deleted_at.is_(None))) or 0
    return {"id": ws.id, "name": ws.name, "personal": ws.personal, "role": role, "permissions": sorted(P.WORKSPACE_ROLE_PERMISSIONS.get(role, frozenset())),
            "members": members, "inspections": inspections, "created_at": iso(ws.created_at)}


@router.get("/workspaces")
def list_workspaces(u: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                      .where(WorkspaceMember.user_id == u.id, Workspace.archived_at.is_(None)).order_by(Workspace.personal.desc(), Workspace.created_at)).all()
    return [_ws_out(db, ws, role) for ws, role in rows]


class WorkspaceIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.user_id == u.id, WorkspaceMember.role == "OWNER")) >= 20:
        raise api_error(409, E.CONFLICT, "You already own the maximum number of workspaces (20).")
    ws = Workspace(name=body.name.strip(), owner_id=u.id, personal=False)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="OWNER"))
    audit(db, "workspace.created", "workspace", ws.id, user_id=u.id, meta={"name": ws.name})
    db.commit()
    return _ws_out(db, ws, "OWNER")


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = workspace_for_user(db, workspace_id, u)
    return _ws_out(db, ws, workspace_role(db, ws.id, u))


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, body: WorkspaceIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = workspace_for_user(db, workspace_id, u, manage=True)
    old = ws.name
    ws.name = body.name.strip()
    audit(db, "workspace.renamed", "workspace", ws.id, user_id=u.id, meta={"from": old, "to": ws.name})
    db.commit()
    return _ws_out(db, ws, workspace_role(db, ws.id, u))


@router.get("/workspaces/{workspace_id}/members")
def workspace_members(workspace_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = workspace_for_user(db, workspace_id, u)
    rows = db.execute(select(User, WorkspaceMember.role, WorkspaceMember.created_at).join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                      .where(WorkspaceMember.workspace_id == ws.id).order_by(WorkspaceMember.created_at)).all()
    return [{"user_id": x.id, "name": x.name, "email": x.email, "role": role, "joined_at": iso(at)} for x, role, at in rows]


class MemberIn(BaseModel):
    email: str = Field(..., max_length=254)
    role: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        v = v.upper()
        if v not in P.WORKSPACE_ROLES or v == "OWNER":
            raise ValueError("Role must be one of ADMIN, ML_ENGINEER, REVIEWER, OPERATOR, VIEWER.")
        return v


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_workspace_member(workspace_id: str, body: MemberIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = workspace_for_user(db, workspace_id, u, manage=True)
    if ws.personal:
        raise api_error(409, E.CONFLICT, "Personal workspaces can't be shared. Create a team workspace instead.")
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if not target:
        raise api_error(404, E.NOT_FOUND, "No user with that email. They need to create an account first.", field="email")
    m = db.get(WorkspaceMember, (ws.id, target.id))
    if m and m.role == "OWNER":
        raise api_error(409, E.CONFLICT, "The owner's role can't be changed.")
    if m:
        m.role = body.role
    else:
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=target.id, role=body.role))
    audit(db, "workspace.member_set", "workspace", ws.id, user_id=u.id, meta={"member": target.id, "role": body.role})
    db.commit()
    return {"user_id": target.id, "name": target.name, "email": target.email, "role": body.role}


@router.delete("/workspaces/{workspace_id}/members/{user_id}")
def remove_workspace_member(workspace_id: str, user_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = workspace_for_user(db, workspace_id, u, manage=True)
    m = db.get(WorkspaceMember, (ws.id, user_id))
    if not m:
        raise api_error(404, E.NOT_FOUND, "Not a member.")
    if m.role == "OWNER":
        raise api_error(409, E.CONFLICT, "The workspace owner can't be removed.")
    db.delete(m)
    audit(db, "workspace.member_removed", "workspace", ws.id, user_id=u.id, meta={"member": user_id})
    db.commit()
    return {"ok": True}


# ====================================================================== audit trail
def _audit_scope(db: Session, u: User):
    """Your own events, plus events on inspections in workspaces you belong to."""
    ws_ids = select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == u.id)
    insp_ids = select(Inspection.id).where(Inspection.workspace_id.in_(ws_ids))
    return select(AuditEvent).where(or_(AuditEvent.user_id == u.id, (AuditEvent.resource_type == "inspection") & AuditEvent.resource_id.in_(insp_ids)))


def _audit_out(r: AuditEvent, users: dict) -> dict:
    return {"id": r.id, "at": iso(r.at), "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id, "result": r.result,
            "meta": r.meta, "user_id": r.user_id, "user_name": users.get(r.user_id), "request_id": r.request_id, "ip": r.ip}


@router.get("/audit-log")
def audit_log(action: str | None = Query(None, max_length=60), start: datetime | None = None, end: datetime | None = None,
              result: str | None = Query(None, pattern="^(success|failure|partial)$"), page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
              u: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = _audit_scope(db, u)
    if action:
        stmt = stmt.where(AuditEvent.action.like(action.replace("%", "") + "%"))
    if start:
        stmt = stmt.where(AuditEvent.at >= start)
    if end:
        stmt = stmt.where(AuditEvent.at < end)
    if result:
        stmt = stmt.where(AuditEvent.result == result)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(AuditEvent.at.desc()).offset((page - 1) * per_page).limit(per_page)))
    users = {x.id: x.name for x in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))} if rows else {}
    return {"items": [_audit_out(r, users) for r in rows], "total": total, "page": page}


@router.get("/audit-log/{event_id}")
def audit_event(event_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = db.scalar(_audit_scope(db, u).where(AuditEvent.id == event_id))
    if not ev:
        raise api_error(404, E.NOT_FOUND, "Audit event not found.")
    who = db.get(User, ev.user_id) if ev.user_id else None
    return {**_audit_out(ev, {ev.user_id: who.name if who else None}), "immutable": True}


@router.get("/audit-export.csv")
def audit_export(start: datetime | None = None, end: datetime | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = _audit_scope(db, u)
    if start:
        stmt = stmt.where(AuditEvent.at >= start)
    if end:
        stmt = stmt.where(AuditEvent.at < end)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "at", "action", "result", "resource_type", "resource_id", "user_id", "request_id"])

    def safe(v):
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    for r in db.scalars(stmt.order_by(AuditEvent.at).limit(100_000)):
        w.writerow([safe(x) for x in (r.id, iso(r.at), r.action, r.result, r.resource_type, r.resource_id, r.user_id, r.request_id)])
    audit(db, "audit.exported", "user", u.id, user_id=u.id)
    db.commit()
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit.csv"'})


# ====================================================================== inspection media (authorised)
@router.get("/media/inspection/{media_id}/{variant}")
def inspection_media(media_id: str, variant: str, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..inspection.service import media_response
    return media_response(db, request, u, media_id, variant)


# ====================================================================== health & system
def _check(fn) -> dict:
    t = time.perf_counter()
    try:
        detail = fn()
        return {"status": "ok", "latency_ms": round((time.perf_counter() - t) * 1000, 1), **({"detail": detail} if detail else {})}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "latency_ms": round((time.perf_counter() - t) * 1000, 1), "error": type(e).__name__}


def _db_check():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"dialect": db.get_bind().dialect.name}
    finally:
        db.close()


def _storage_check():
    key = f"health/{secrets.token_hex(8)}.txt"
    payload = secrets.token_bytes(16)
    storage.put(key, payload, "text/plain")
    try:
        if storage.get(key) != payload:
            raise RuntimeError("read-back mismatch")
    finally:
        storage.delete(key)
    return {"backend": settings.storage_backend}


def _ml_check() -> dict:
    t = time.perf_counter()
    try:
        import torch
        import torchvision
        cuda = torch.cuda.is_available()
        return {"status": "ok", "torch": torch.__version__, "torchvision": torchvision.__version__, "cuda": cuda,
                "gpu": torch.cuda.get_device_name(0) if cuda else None, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": type(e).__name__}


def _model_check() -> dict:
    from ..inspection import analysis as AN
    eng, why = AN.engine()
    if eng is None:
        return {"status": "degraded", "message": why}
    d = eng.describe()
    gate = AN.gate_status()
    return {"status": "ok" if d["available"] else "error", "model_id": d["model_id"], "state": d["state"], "classes": d["classes"],
            "domain_gate": gate.get("state"), **({"error": d["error"]} if d.get("error") else {})}


@router.get("/health/live")
async def health_live():
    """Liveness: the process is up and serving. No dependencies are touched. Served on the event loop, not the worker
    thread pool, so analyses queued for the single detector slot can't make a busy server look dead."""
    return {"status": "ok", "uptime_s": round((datetime.now(timezone.utc) - STARTED).total_seconds(), 1)}


@router.get("/health/ready")
def health_ready():
    """Readiness: database and image store are critical (503 when down); the detection model and ML runtime degrade."""
    checks = {"database": _check(_db_check), "object_storage": _check(_storage_check), "detection_model": _model_check(), "ml_runtime": _ml_check()}
    critical_ok = all(checks[k]["status"] == "ok" for k in ("database", "object_storage"))
    degraded = [k for k, v in checks.items() if v["status"] != "ok"]
    status = "unavailable" if not critical_ok else ("degraded" if degraded else "ok")
    return JSONResponse({"status": status, "checks": checks, "degraded": degraded, "checked_at": iso(datetime.now(timezone.utc))},
                        status_code=503 if status == "unavailable" else 200)


@router.get("/system/version")
def system_version():
    return {"name": "VisionTra", "api_version": API_VERSION, "api_prefixes": ["/api", "/api/v1"], "python": _platform.python_version(),
            "build": os.environ.get("VISIONTRA_BUILD") or None, "environment": settings.env,
            "frontend_build": _main().FRONTEND_BUILD_AT_START, "started_at": _main().STARTED_AT}


def _main():
    from .. import main as M
    return M


@router.get("/system/hardware")
def system_hardware(u: User = Depends(current_user)):
    import torch
    info = {"cpu": _platform.processor() or _platform.machine(), "cpu_count": os.cpu_count(), "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available()}
    if info["cuda_available"]:
        info["gpu"] = torch.cuda.get_device_name(0)
        info["cuda_version"] = torch.version.cuda
    return info


@router.get("/system/capabilities")
def system_capabilities(u: User = Depends(current_user)):
    from ..inspection import analysis as AN
    eng, why = AN.engine()
    if eng is None:
        dm = {"available": False, "reason": why}
    else:
        d = eng.describe()
        dm = {"available": d["available"], "model_id": d["model_id"], "classes": d["classes"], "readiness": d["readiness"],
              "review_required": True, "endpoint": "/api/v1/detection-model", "domain_gate": AN.gate_status().get("state")}
    return {
        "product": "VisionTra aircraft surface inspection",
        "storage": {"backend": settings.storage_backend},
        "inspection": {"persistence": True, "reviews": True, "reports": True, "detection_model": dm, "detection_datasets": True, "analytics": True,
                       "demo_samples": {"allowed": False},
                       "policy": {"formats": sorted({v[0] for k, v in M.FORMATS.items() if k != "HEIF" or M.register_heif_opener is not None}),
                                  "extensions": sorted({v[1] for k, v in M.FORMATS.items() if k != "HEIF" or M.register_heif_opener is not None} | {"jpeg", "tiff"}
                                                       | ({"heif"} if M.register_heif_opener is not None else set())),
                                  "heic_supported": M.register_heif_opener is not None,
                                  "stored_as_is": sorted({v[0] for v in M.STORED_AS_IS.values()}),
                                  "max_upload_mb": settings.inspection_max_upload_mb, "min_side": settings.inspection_min_side,
                                  "max_side": settings.inspection_max_side, "max_pixels": settings.max_image_pixels}},
    }
