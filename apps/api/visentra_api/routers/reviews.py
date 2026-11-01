"""Human review API (Phase 11; Phase 5 contract EP-12…EP-16, docs/PHASE_11_REVIEW_API.md).

    POST /api/v1/inspections/{id}/decisions   If-Match; JSON {target: {type, id}, kind, decision?, corrected_class_id?, comment?}
    POST /api/v1/inspections/{id}/findings    If-Match; JSON {media_id, category, box: {x, y, w, h}, comment?}
    GET  /api/v1/inspections/{id}/decisions   review history (append-only events)
    POST /api/v1/inspections/{id}/signoff     If-Match; JSON {note?}
    POST /api/v1/inspections/{id}/reopen      If-Match; JSON {comment?}

The reviewer is always the signed-in principal; no request field can name another reviewer. Optional Idempotency-Key.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..inspection import review as RV
from ..models import User
from ..security import current_user, limiter

router = APIRouter(prefix="/api", tags=["review"])
RATE = 120          # review actions per user per minute (API_ARCHITECTURE §6)


@router.post("/inspections/{inspection_id}/decisions")
def post_decision(inspection_id: str, request: Request, body: dict = Body(...), u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"review:{u.id}", RATE, 60)
    return RV.decide(db, request, u, inspection_id, body)


@router.post("/inspections/{inspection_id}/decisions/bulk")
def post_decisions_bulk(inspection_id: str, request: Request, body: dict = Body(...), u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"review:{u.id}", RATE, 60)          # one request, counted once
    return RV.decide_bulk(db, request, u, inspection_id, body)


@router.post("/inspections/{inspection_id}/findings", status_code=201)
def post_finding(inspection_id: str, request: Request, body: dict = Body(...), u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"review:{u.id}", RATE, 60)
    return RV.add_finding(db, request, u, inspection_id, body)


@router.get("/inspections/{inspection_id}/decisions")
def get_decisions(inspection_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return RV.history(db, u, inspection_id)


@router.post("/inspections/{inspection_id}/signoff")
def post_signoff(inspection_id: str, request: Request, body: dict = Body(default={}), u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"review:{u.id}", RATE, 60)
    return RV.sign_off(db, request, u, inspection_id, body or {})


@router.post("/inspections/{inspection_id}/reopen")
def post_reopen(inspection_id: str, request: Request, body: dict = Body(default={}), u: User = Depends(current_user), db: Session = Depends(get_db)):
    limiter.check(f"review:{u.id}", RATE, 60)
    return RV.reopen(db, request, u, inspection_id, body or {})
