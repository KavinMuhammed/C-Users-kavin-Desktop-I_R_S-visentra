"""Inspection analytics API (Phase 12; Phase 5 contract EP-31).

    GET /api/v1/inspection-analytics?workspace_id=   counts, decisions, categories, reports, timeline, attention list

Server records only; see inspection/analytics.py for every definition.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..inspection import analytics as AN
from ..models import User
from ..security import current_user

router = APIRouter(prefix="/api", tags=["inspection-analytics"])


@router.get("/inspection-analytics")
def inspection_analytics(workspace_id: Optional[str] = Query(None, max_length=64), u: User = Depends(current_user), db: Session = Depends(get_db)):
    return AN.summary(db, u, workspace_id)
