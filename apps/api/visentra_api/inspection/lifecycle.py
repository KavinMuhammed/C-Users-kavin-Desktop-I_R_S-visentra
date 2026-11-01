"""Inspection lifecycle — the only place that changes `inspections.status` or bumps `inspections.revision`.

States and transitions are the Phase 5 state machine `inspections.status` (docs/architecture/architecture_catalog.json,
mirrored by the CHECK constraint). A test asserts that TRANSITIONS equals the catalog.

Every change is a conditional UPDATE:
    UPDATE inspections SET ..., revision = revision + 1
     WHERE id = :id AND deleted_at IS NULL AND status IN (:allowed_from) [AND revision = :expected]
If no row matches, nothing was written: the caller gets 409 REVIEW_CONFLICT (stale revision) or 409 CONFLICT
(transition not allowed from the current status). A stale writer therefore can never overwrite a newer state.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import errors as E
from ..errors import api_error
from ..models import INSPECTION_STATUSES, Inspection, now

STATES = INSPECTION_STATUSES
INITIAL = "created"
TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("created", "analysing"), ("created", "awaiting_review"),
    ("analysing", "awaiting_review"), ("analysing", "analysis_failed"),
    ("analysis_failed", "analysing"), ("analysis_failed", "in_review"),
    ("awaiting_review", "analysing"), ("awaiting_review", "in_review"),
    ("in_review", "reviewed"), ("reviewed", "in_review"),
})


def allowed(frm: str, to: str) -> bool:
    return (frm, to) in TRANSITIONS


def sources_of(to: str) -> list[str]:
    return sorted(a for a, b in TRANSITIONS if b == to)


def creation_path(analysis_mode: str) -> list[str]:
    """Status path applied inside the creating transaction. With no detection model the inspection goes straight to
    review (`created -> awaiting_review`); `model` mode goes `created -> analysing` and the caller appends the outcome
    (`awaiting_review` or `analysis_failed`) — Phase 13 analyses before the creating transaction."""
    path = [INITIAL, "analysing" if analysis_mode == "model" else "awaiting_review"]
    assert allowed(path[0], path[1])
    return path


def _conflict(db: Session, insp_id: str, expected: int | None, to: str | None) -> Exception:
    cur = db.execute(select(Inspection.status, Inspection.revision, Inspection.deleted_at).where(Inspection.id == insp_id)).first()
    if cur is None or cur.deleted_at is not None:
        return api_error(404, E.INSPECTION_NOT_FOUND, "This inspection does not exist or is not visible to you.")
    if expected is not None and cur.revision != expected:
        return api_error(409, E.REVIEW_CONFLICT, "Someone else changed this inspection. Reload to see the latest version.",
                         current_revision=cur.revision, your_revision=expected, current_status=cur.status)
    return api_error(409, E.CONFLICT, f"An inspection cannot move from '{cur.status}' to '{to}'.", current_status=cur.status, requested_status=to,
                     allowed_from=sources_of(to) if to else [])


def transition(db: Session, insp_id: str, to: str, *, expected_revision: int | None = None, error_code: str | None = None,
               fields: dict[str, Any] | None = None) -> int:
    """Move one inspection to `to`. Returns the new revision. Raises 404/409 without writing anything."""
    if to not in STATES:
        raise ValueError(f"unknown status {to!r}")
    frm = sources_of(to)
    values: dict[str, Any] = {"status": to, "revision": Inspection.revision + 1, "updated_at": now(), **(fields or {})}
    values["last_error_code"] = error_code if to == "analysis_failed" else None
    stmt = update(Inspection).where(Inspection.id == insp_id, Inspection.deleted_at.is_(None), Inspection.status.in_(frm))
    if expected_revision is not None:
        stmt = stmt.where(Inspection.revision == expected_revision)
    res = db.execute(stmt.values(**values).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise _conflict(db, insp_id, expected_revision, to)
    return db.scalar(select(Inspection.revision).where(Inspection.id == insp_id))


def update_fields(db: Session, insp_id: str, expected_revision: int, fields: dict[str, Any]) -> int:
    """Change metadata (not status) under optimistic concurrency. Returns the new revision."""
    if "status" in fields or "revision" in fields:
        raise ValueError("status and revision change only through transition()")
    stmt = (update(Inspection).where(Inspection.id == insp_id, Inspection.deleted_at.is_(None), Inspection.revision == expected_revision)
            .values(**fields, revision=Inspection.revision + 1, updated_at=now()).execution_options(synchronize_session=False))
    if db.execute(stmt).rowcount != 1:
        raise _conflict(db, insp_id, expected_revision, None)
    return expected_revision + 1
