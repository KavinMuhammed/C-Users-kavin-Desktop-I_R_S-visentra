"""Audit trail. Events are written in the same transaction as the action they describe.

The audit table is append-only through the API (no update/delete endpoints)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .logs import request_id_var
from .models import AuditEvent


def audit(db: Session, action: str, resource_type: str, resource_id: str | None = None, *, user_id: str | None = None,
          project_id: str | None = None, result: str = "success", meta: dict[str, Any] | None = None, ip: str | None = None) -> AuditEvent:
    ev = AuditEvent(action=action, resource_type=resource_type, resource_id=resource_id, user_id=user_id, project_id=project_id,
                    result=result, meta=meta or {}, request_id=request_id_var.get(), ip=ip)
    db.add(ev)
    db.flush()
    return ev
