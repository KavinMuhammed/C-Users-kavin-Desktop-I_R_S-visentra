"""storage_objects: write intents and reconciliation between the object store and the database
(docs/DATA_FLOW_AND_CONSISTENCY.md §3).

Protocol for every object the inspection module writes:
  TX1  insert storage_objects(state='pending')          — the intent is durable before any byte is written
  PUT  write the object (atomic per object)
  TX2  insert the rows that reference the key and set the intent to 'committed' (same transaction)
On a failure after PUT the request compensates at once (delete the object, intent -> 'deleted'). If the process
dies instead, the reconciler finds the 'pending' intent after a grace period and resolves it:
  · a database row references the key  -> 'committed' (TX2 succeeded; only the intent update was lost — cannot happen
                                          today because both are in TX2, kept for safety)
  · nothing references it               -> delete the object (if it exists) and mark 'deleted'
With `verify=True` it also checks that every committed object still exists and has its recorded size; a missing one
becomes 'missing' and the media row's integrity is set to 'missing' (never silently "fixed").
The reconciler never lists the bucket and never touches an object that has no intent row.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import InspectionMedia, MediaRendition, StorageObject, now

log = logging.getLogger("visentra.storage")


def register_pending(db: Session, items: Iterable[tuple[str, str, str]], request_id: str | None) -> None:
    """items: (object_key, purpose, content_type). Caller commits (TX1)."""
    for key, purpose, ctype in items:
        db.add(StorageObject(object_key=key, store="objects", purpose=purpose, state="pending", content_type=ctype, request_id=(request_id or None) and request_id[:32]))


def mark_committed(db: Session, key: str, sha256: str, size: int, owner_type: str, owner_id: str) -> None:
    n = db.execute(update(StorageObject).where(StorageObject.object_key == key, StorageObject.state == "pending")
                   .values(state="committed", sha256=sha256, size_bytes=size, owner_type=owner_type, owner_id=owner_id, committed_at=now())).rowcount
    if n != 1:
        raise RuntimeError("storage intent missing or not pending")


def compensate(db_factory, store, keys: list[str]) -> None:
    """Best effort, after a failed request: delete written objects and close their intents. Errors are left to the reconciler."""
    for k in keys:
        try:
            store.delete(k)
        except Exception:      # pragma: no cover - store outage: the reconciler retries
            log.warning("compensation could not delete an object; the reconciler will retry")
            continue
        try:
            with db_factory() as db:
                db.execute(update(StorageObject).where(StorageObject.object_key == k, StorageObject.state == "pending").values(state="deleted", deleted_at=now()))
                db.commit()
        except Exception:      # pragma: no cover
            log.warning("compensation could not close an intent; the reconciler will retry")


def _referenced(db: Session, key: str) -> tuple[str, str] | None:
    m = db.scalar(select(InspectionMedia.id).where(InspectionMedia.object_key == key))
    if m:
        return "inspection_media", m
    r = db.scalar(select(MediaRendition.id).where(MediaRendition.object_key == key))
    if r:
        return "media_rendition", r
    from ..models import InspectionReport     # Phase 11: report PDFs follow the same intent protocol
    rep = db.scalar(select(InspectionReport.id).where(InspectionReport.object_key == key))
    if rep:
        return "inspection_report", rep
    return None


@dataclass
class ReconcileReport:
    pending_seen: int = 0
    committed_late: int = 0
    orphans_deleted: int = 0
    orphans_absent: int = 0
    verified: int = 0
    missing: list[str] = field(default_factory=list)
    size_mismatch: list[str] = field(default_factory=list)
    temp_removed: int = 0
    errors: int = 0
    dry_run: bool = False

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["missing"], d["size_mismatch"] = len(self.missing), len(self.size_mismatch)   # counts only: keys are internal
        return d


def reconcile(db: Session, store, *, grace_s: int | None = None, verify: bool = False, dry_run: bool = False) -> ReconcileReport:
    rep = ReconcileReport(dry_run=dry_run)
    cutoff = now() - timedelta(seconds=settings.storage_intent_grace_s if grace_s is None else grace_s)
    rows = db.scalars(select(StorageObject).where(StorageObject.state.in_(("pending", "delete_pending")), StorageObject.created_at <= cutoff)).all()
    for so in rows:
        rep.pending_seen += 1
        try:
            ref = _referenced(db, so.object_key)
            if ref and so.state == "pending":
                rep.committed_late += 1
                if not dry_run:
                    data = store.get(so.object_key)
                    so.state, so.sha256, so.size_bytes, so.owner_type, so.owner_id, so.committed_at = "committed", hashlib.sha256(data).hexdigest(), len(data), ref[0], ref[1], now()
                continue
            exists = store.exists(so.object_key)
            if exists:
                rep.orphans_deleted += 1
            else:
                rep.orphans_absent += 1
            if not dry_run:
                if exists:
                    store.delete(so.object_key)
                so.state, so.deleted_at = "deleted", now()
        except Exception:
            rep.errors += 1
            log.exception("reconcile failed for one intent")
    if verify:
        for so in db.scalars(select(StorageObject).where(StorageObject.state == "committed")).all():
            rep.verified += 1
            if not store.exists(so.object_key):
                rep.missing.append(so.object_key)
                if not dry_run:
                    so.state = "missing"
                    db.execute(update(InspectionMedia).where(or_(InspectionMedia.object_key == so.object_key,
                                                                 InspectionMedia.id.in_(select(MediaRendition.media_id).where(MediaRendition.object_key == so.object_key))))
                               .values(integrity="missing", integrity_checked_at=now()))
            elif so.size_bytes is not None and hasattr(store, "_path") and store._path(so.object_key).stat().st_size != so.size_bytes:
                rep.size_mismatch.append(so.object_key)
                if not dry_run:
                    db.execute(update(InspectionMedia).where(InspectionMedia.object_key == so.object_key).values(integrity="checksum_mismatch", integrity_checked_at=now()))
    if hasattr(store, "sweep_temp") and not dry_run:
        rep.temp_removed = store.sweep_temp(max(60, settings.storage_intent_grace_s))
    if not dry_run:
        db.commit()
    return rep
