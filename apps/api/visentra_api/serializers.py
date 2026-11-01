"""Response shaping for accounts. Storage keys and filesystem paths are never returned."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import User


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset (SQLite returns naive datetimes; all stored times are UTC)."""
    if not dt:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "created_at": iso(u.created_at)}
