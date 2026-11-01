"""Idempotency keys for expensive or non-repeatable POSTs (training creation, promotion, device events,
inspection creation, experimental runs).

A client sends `Idempotency-Key: <unique string>`. The first successful response is stored; a retry
with the same key (same principal + endpoint) returns that stored response instead of acting twice.

Phase 10: the stored row also keeps `request_hash` (a SHA-256 of what the request asked for). The same key with a
different request is refused with 409 IDEMPOTENCY_CONFLICT instead of silently replaying an unrelated answer.
Rows written before Phase 10 have no hash and replay as before.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import errors as E
from ..errors import api_error
from ..models import IdempotencyKey


def _key(request: Request) -> str | None:
    k = request.headers.get("idempotency-key")
    return k.strip()[:120] if k and k.strip() else None


def has_key(request: Request) -> bool:
    return _key(request) is not None


def request_hash(request: Request, payload: Any) -> str:
    """Hash of method, route path and a JSON-serialisable description of the request (include file digests, not bytes)."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{request.method}\n{request.url.path}\n{body}".encode()).hexdigest()


def replay(db: Session, request: Request, scope: str, req_hash: str | None = None) -> JSONResponse | None:
    k = _key(request)
    if not k:
        return None
    row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == k))
    if row is None:
        return None
    if row.request_hash and req_hash and row.request_hash != req_hash:
        raise api_error(409, E.IDEMPOTENCY_CONFLICT, "This Idempotency-Key was already used for a different request. Start the action again with a new key.")
    return JSONResponse(row.response, status_code=row.status_code, headers={"Idempotent-Replay": "true"})


def store(db: Session, request: Request, scope: str, status_code: int, response: Any, req_hash: str | None = None) -> None:
    k = _key(request)
    if not k:
        return
    try:
        with db.begin_nested():
            db.add(IdempotencyKey(scope=scope, key=k, status_code=status_code, response=response, request_hash=req_hash))
    except IntegrityError:
        pass  # a concurrent request stored it first; the unique constraint keeps one result


def claim(db: Session, request: Request, scope: str, status_code: int, response: Any, req_hash: str | None) -> None:
    """Strict variant for requests whose effect must happen at most once: the row is added inside the caller's
    transaction, so a concurrent duplicate makes the caller's COMMIT fail (IntegrityError) and nothing of it persists."""
    k = _key(request)
    if k:
        db.add(IdempotencyKey(scope=scope, key=k, status_code=status_code, response=response, request_hash=req_hash))
