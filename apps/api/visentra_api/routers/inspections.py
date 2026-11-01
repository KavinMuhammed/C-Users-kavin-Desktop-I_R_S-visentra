"""Inspection persistence API (Phase 10; Phase 5 contract EP-01, EP-06…EP-10, docs/API_ARCHITECTURE.md).

Declared under /api and served as /api/v1 by the version alias middleware.

    POST   /api/v1/inspections                      multipart: file, component, zone?, aircraft_ref?, notes?, project_id?, workspace_id?, analyse?
    GET    /api/v1/inspections                      ?workspace_id&q&component&status&category&from&to&mine&sort&dir&page&per_page
    GET    /api/v1/inspections/{inspection_id}
    GET    /api/v1/inspections/{inspection_id}/status
    PATCH  /api/v1/inspections/{inspection_id}      If-Match: <revision>; JSON {component?, zone?, aircraft_ref?, notes?}
    DELETE /api/v1/inspections/{inspection_id}      soft delete (OWNER/ADMIN)

Media: GET /api/media/inspection/{media_id}/{display|thumb|original} (routers/insights.py → inspection/service.py).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from .. import errors as E
from ..config import settings
from ..db import get_db
from ..errors import api_error
from ..inspection import service as SVC
from ..models import User
from ..security import current_user, limiter

router = APIRouter(prefix="/api", tags=["inspections"])

# Uploads and re-analyses wait for the single detector slot inside a worker thread. Without their own allowance they could
# occupy the whole shared pool (40 threads) and stall every other endpoint, readiness included. Extra requests queue on the
# event loop instead; the detector's own ANALYSIS_BUSY timeout still applies once they hold a thread.
ANALYSIS_THREADS = 8
_analysis_limiter = None


def _analysis_threads():
    global _analysis_limiter
    if _analysis_limiter is None:
        import anyio
        _analysis_limiter = anyio.CapacityLimiter(ANALYSIS_THREADS)
    return _analysis_limiter


async def _run_analysis_work(fn, *args):
    import anyio
    from functools import partial
    return await anyio.to_thread.run_sync(partial(fn, *args), limiter=_analysis_threads())


async def _read_limited(upload: UploadFile, limit: int) -> bytes | None:
    """Read an upload without holding more than `limit` + 1 MB; None when it is larger than `limit`."""
    chunks, total = [], 0
    while True:
        c = await upload.read(1024 * 1024)
        if not c:
            break
        total += len(c)
        if total > limit:
            return None
        chunks.append(c)
    return b"".join(chunks)


@router.post("/inspections", status_code=201)
async def create_inspection(request: Request, file: UploadFile = File(...), component: str = Form(""), zone: Optional[str] = Form(None),
                            aircraft_ref: Optional[str] = Form(None), notes: Optional[str] = Form(None), project_id: Optional[str] = Form(None),
                            workspace_id: Optional[str] = Form(None), analyse: Optional[str] = Form("true"),
                            u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"inspection-create:{u.id}", settings.inspection_rate_per_min, 60)
    data = await _read_limited(file, settings.inspection_max_upload_mb * 1024 * 1024)
    if data is None:
        raise api_error(413, "TOO_LARGE", f"The image is larger than {settings.inspection_max_upload_mb} MB. Nothing was saved.")
    form = {"component": component, "zone": zone, "aircraft_ref": aircraft_ref, "notes": notes, "project_id": project_id,
            "workspace_id": workspace_id, "analyse": analyse}
    # validation, renditions, storage and the transactions are blocking work: run them off the event loop
    return await _run_analysis_work(SVC.create, db, request, u, data, file.filename, form)


@router.get("/inspections")
def list_inspections(workspace_id: Optional[str] = Query(None, max_length=64), q: Optional[str] = Query(None), component: Optional[str] = None,
                     status: Optional[str] = None, category: Optional[str] = None, date_from: Optional[str] = Query(None, alias="from"),
                     date_to: Optional[str] = Query(None, alias="to"), mine: bool = False, sort: str = "created_at", dir: str = "desc",
                     page: int = Query(1, ge=1, le=100000), per_page: int = Query(25, ge=1, le=100),
                     u: User = Depends(current_user), db: Session = Depends(get_db)):
    return SVC.list_inspections(db, u, workspace_id=workspace_id, q=q, component=component or None, status=status or None, category=category or None,
                                date_from=date_from, date_to=date_to, mine=mine, sort=sort, direction=dir, page=page, per_page=per_page)


@router.get("/inspections/{inspection_id}")
def get_inspection(inspection_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    insp, role = SVC.inspection_for_user(db, inspection_id, u)
    return JSONResponse(SVC.full_out(db, insp, role), headers={"ETag": f'"{insp.revision}"', "Cache-Control": "no-store"})


@router.get("/inspections/{inspection_id}/status")
def inspection_status(inspection_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    insp, _ = SVC.inspection_for_user(db, inspection_id, u)
    return JSONResponse({"id": insp.id, "status": insp.status, "revision": insp.revision, "analysis_mode": insp.analysis_mode, "analysis": None,
                         "updated_at": SVC.iso(insp.updated_at)}, headers={"ETag": f'"{insp.revision}"', "Cache-Control": "no-store"})


@router.get("/inspections/{inspection_id}/evidence")
def inspection_evidence(inspection_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Download a revision-labelled, workspace-scoped evidence handoff without changing the inspection."""
    from ..inspection import evidence
    packet = evidence.build(db, u, inspection_id)
    revision = packet["payload"]["inspection"]["revision"]
    return JSONResponse(packet, headers={"Cache-Control": "no-store", "ETag": f'"{revision}"',
        "Content-Disposition": f'attachment; filename="visiontra-evidence-r{revision}.json"'})


@router.patch("/inspections/{inspection_id}")
def patch_inspection(inspection_id: str, request: Request, body: dict = Body(...), u: User = Depends(current_user), db: Session = Depends(get_db)):
    out = SVC.patch(db, request, u, inspection_id, body)
    return JSONResponse(out, headers={"ETag": f'"{out["revision"]}"'})


@router.delete("/inspections/{inspection_id}", status_code=204)
def delete_inspection(inspection_id: str, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    SVC.soft_delete(db, request, u, inspection_id)
    return Response(status_code=204)


# Phase 13 — EP-03 / EP-04: operational, review-assisted analysis
@router.post("/inspections/{inspection_id}/analyses", status_code=201)
async def analyse_inspection(inspection_id: str, request: Request, payload: Optional[dict] = Body(None), u: User = Depends(current_user),
                             db: Session = Depends(get_db)):
    """Run detection again. {"force": true, "reason": "…"} runs it even when the aircraft-surface check rejected the image (reason required, audited; later re-runs keep the override)."""
    from ..inspection import analysis as AN
    limiter.check(f"inspection-analyse:{u.id}", settings.inspection_rate_per_min, 60)
    force = bool((payload or {}).get("force")) if isinstance(payload, dict) else False
    reason = (payload or {}).get("reason") if isinstance(payload, dict) and isinstance((payload or {}).get("reason"), str) else None
    body, rev = await _run_analysis_work(AN.rerun, db, request, u, inspection_id, force, reason)
    return JSONResponse(body, status_code=201, headers={"ETag": f'"{rev}"', "Cache-Control": "no-store"})


@router.get("/inspections/{inspection_id}/analyses")
def list_analyses(inspection_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..inspection import analysis as AN
    return JSONResponse(AN.list_runs(db, u, inspection_id), headers={"Cache-Control": "no-store"})


@router.get("/detection-model")
def detection_model(u: User = Depends(current_user)):
    """The model that analyses new inspections, its readiness, thresholds and recorded held-out evaluation."""
    from ..inspection import analysis as AN
    return JSONResponse(AN.status(), headers={"Cache-Control": "no-store"})


@router.get("/defect-guidance")
def defect_guidance(u: User = Depends(current_user)):
    """Phase 16: what a dent / crack / corrosion finding means, immediate actions, repair options and prevention
    (general published guidance with sources; the SRM/AMM and a certifying engineer decide)."""
    from ..inspection import guidance as G
    return JSONResponse(G.all_guidance(), headers={"Cache-Control": "private, max-age=3600"})


@router.get("/inspections/{inspection_id}/text-evidence")
def text_evidence_list(inspection_id: str, q: str = Query("", max_length=200),
                       u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..inspection import text_evidence as T
    return JSONResponse(T.list_evidence(db, u, inspection_id, q), headers={"Cache-Control": "no-store"})


@router.post("/inspections/{inspection_id}/text-evidence")
async def extract_text_evidence(inspection_id: str, file: Optional[UploadFile] = File(None),
                               document_revision: Optional[str] = Form(None),
                               u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..inspection import text_evidence as T, ocr
    SVC.inspection_for_user(db, inspection_id, u, "inspection.review")
    limiter.check(f"ocr:{u.id}", 10, 60)
    data = await _read_limited(file, ocr.MAX_BYTES) if file else None
    if file and data is None:
        raise api_error(413, "OCR_TOO_LARGE", "Text documents are limited to 10 MB.")
    result = await run_in_threadpool(T.create, db, u, inspection_id, data, file.filename if file else None, document_revision)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.patch("/inspections/{inspection_id}/text-evidence/{evidence_id}")
def correct_text_evidence(inspection_id: str, evidence_id: str, request: Request, body: dict = Body(...),
                          u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..inspection import text_evidence as T
    return JSONResponse(T.correct(db, request, u, inspection_id, evidence_id, body), headers={"Cache-Control": "no-store"})


@router.get("/inspections/{inspection_id}/text-evidence/{evidence_id}/source")
def text_source(inspection_id: str, evidence_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    import hashlib
    from ..inspection import text_evidence as T
    from ..storage import storage
    row = T.get_row(db, u, inspection_id, evidence_id, "media.read_original")
    try:
        data = storage.get(row.object_key)
    except OSError:
        raise api_error(503, "STORAGE_UNAVAILABLE", "The source document cannot be read.") from None
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise api_error(409, "INTEGRITY_FAILED", "Source checksum mismatch.")
    return Response(data, media_type=row.mime, headers={"Cache-Control": "no-store",
                    "Content-Disposition": 'attachment; filename="text-evidence-source"',
                    "X-Content-Type-Options": "nosniff"})
