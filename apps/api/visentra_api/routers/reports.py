"""Server report API (Phase 11; Phase 5 contract EP-17…EP-19, docs/PHASE_11_REPORT_STORAGE.md).

    POST /api/v1/inspections/{id}/reports     generate for the current (signed-off) revision; repeat → the existing report
    GET  /api/v1/reports/{report_id}          metadata (incl. version and stale flag)
    GET  /api/v1/reports/{report_id}/download the PDF, SHA-256 re-verified, private headers

Reports of an inspection are also listed in the inspection's `reports` field (EP-08).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..reports import service as RS
from ..security import current_user, limiter

router = APIRouter(prefix="/api", tags=["reports"])


@router.post("/inspections/{inspection_id}/reports")
async def create_report(inspection_id: str, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"report:{u.id}", 10, 60)
    return await run_in_threadpool(RS.generate, db, request, u, inspection_id)      # rendering and storage are blocking work


@router.get("/reports/{report_id}")
def get_report(report_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return RS.get(db, u, report_id)


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return RS.download(db, request, u, report_id)
