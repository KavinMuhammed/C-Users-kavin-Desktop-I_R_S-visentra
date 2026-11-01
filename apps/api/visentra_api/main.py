"""Visiontra API application."""
from __future__ import annotations

import gzip
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .logs import request_id_var, setup_logging
from .routers import auth, detection_data, inspection_analytics, inspections, reports, reviews, system

setup_logging(settings.log_level)
log = logging.getLogger("visentra.api")
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _read_frontend_build() -> str | None:
    """The build id of the frontend that existed when this server process started (apps/web/build.py writes it).
    The page compares it with its own id: a mismatch means the server process is older than the page and must be
    restarted (Python code is loaded once, at start-up)."""
    try:
        import json as _json
        return _json.loads((WEB_DIST / "build.json").read_text(encoding="utf-8")).get("build")
    except (OSError, ValueError):
        return None


FRONTEND_BUILD_AT_START = _read_frontend_build()
STARTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    # keep the app's JSON logging: alembic.ini's [loggers] section would otherwise reset the root logger to WARNING with a
    # plain-text handler, silently dropping every INFO/request log after start-up (found in the 27 Oct staging dry run)
    cfg.attributes["configure_logger"] = False
    from alembic.script import ScriptDirectory

    from .dbbackup import backup_before_upgrade
    backup_before_upgrade(ScriptDirectory.from_config(cfg).get_current_head())   # Phase 10: never migrate without a copy
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.env != "test":
        migrate()
        from .inspection import analysis as AN
        AN.warm_up_in_background()     # load the detection model and domain gate without blocking start-up
    yield


app = FastAPI(title="VisionTra API", version="2.0.0", lifespan=lifespan, docs_url="/api/docs" if not settings.is_production else None, redoc_url=None, openapi_url="/api/openapi.json" if not settings.is_production else None)

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"], expose_headers=["X-Request-ID", "Idempotent-Replay"])

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


MAX_BODY = (settings.inspection_max_upload_mb + 2) * 1024 * 1024        # one image per request, plus multipart overhead


@app.middleware("http")
async def body_limit(request: Request, call_next):
    from .inspection.ocr import MAX_BYTES
    is_text_upload = request.method == "POST" and request.url.path.rstrip("/").endswith("/text-evidence")
    limit = MAX_BYTES + 1024 * 1024 if is_text_upload else MAX_BODY
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        maximum = MAX_BYTES // (1024 * 1024) if is_text_upload else settings.inspection_max_upload_mb
        return JSONResponse(_payload("TOO_LARGE", f"The upload is larger than {maximum} MB."), status_code=413)
    return await call_next(request)


@app.middleware("http")
async def api_version_alias(request: Request, call_next):
    """/api/v1/... is the versioned contract; it is served by the same handlers as /api/..."""
    path = request.scope["path"]
    if path == "/api/v1" or path.startswith("/api/v1/"):
        request.scope["path"] = "/api" + path[len("/api/v1"):]
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


_RID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@app.middleware("http")
async def request_context(request: Request, call_next):
    sent = request.headers.get("x-request-id", "")
    rid = sent if 8 <= len(sent) <= 32 and set(sent) <= _RID_OK else secrets.token_hex(8)
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        response: Response = await call_next(request)
        # logged while the request id is still set (it used to be logged after the reset, so request lines had no id)
        if request.url.path.startswith("/api/") and request.url.path != "/api/events":
            log.info("request", extra={"method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - t0) * 1000, 1), "user_id": getattr(request.state, "user_id", None)})
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response.headers.setdefault("Content-Security-Policy", CSP)
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _payload(code: str, message: str, **extra):
    extra.setdefault("details", {})
    return {"error": {"code": code, "message": message, "request_id": request_id_var.get(), **extra}}


@app.exception_handler(StarletteHTTPException)
async def http_exc(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        extra = {k: exc.detail[k] for k in ("field", "details") if k in exc.detail}
        body = _payload(exc.detail.get("code", "ERROR"), exc.detail.get("message", "Request failed."), **extra)
    else:
        body = _payload("NOT_FOUND" if exc.status_code == 404 else "ERROR", str(exc.detail) if exc.status_code < 500 else "Request failed.")
    return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_exc(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or None
    msg = str(first.get("msg", "Invalid input")).replace("Value error, ", "")
    return JSONResponse(_payload("VALIDATION", msg, field=field), status_code=422)


@app.exception_handler(OperationalError)
async def db_busy(request: Request, exc: OperationalError):
    """Phase 10: a database that is momentarily locked/busy is a retryable condition, not a server bug. The failed
    transaction was rolled back, so nothing was changed; say so instead of a bare 500."""
    if "locked" in str(exc.orig).lower() or "busy" in str(exc.orig).lower():
        log.warning("database busy", extra={"path": request.url.path})
        return JSONResponse(_payload("SERVICE_UNAVAILABLE", "The database is busy. Nothing was changed; try again."), status_code=503, headers={"Retry-After": "1"})
    log.exception("database error", extra={"path": request.url.path})
    return JSONResponse(_payload("SERVER", "Something went wrong on the server. The error was logged."), status_code=500)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error", extra={"path": request.url.path})
    return JSONResponse(_payload("SERVER", "Something went wrong on the server. The error was logged."), status_code=500)


for r in (auth, system, inspections, reviews, reports, inspection_analytics, detection_data):
    app.include_router(r.router)


@app.get("/api/health")
async def health():
    """Back-compat liveness probe (event loop, no thread pool). Use /api/health/ready for dependency checks."""
    return {"status": "ok", "worker_mode": settings.worker_mode}


_INDEX_CACHE: dict = {}


def _index_response(index: Path, request: Request) -> Response:
    """Phase 12 (P-04): the single-file frontend is sent gzip-compressed when the browser accepts it, with an ETag so a
    revalidation (`Cache-Control: no-cache`) costs a 304 instead of the whole file. Content is identical either way."""
    st = index.stat()
    key = (st.st_mtime_ns, st.st_size)
    if _INDEX_CACHE.get("key") != key:
        raw = index.read_bytes()
        _INDEX_CACHE.update(key=key, raw=raw, gz=gzip.compress(raw, 6, mtime=0), etag='"' + hashlib.sha256(raw).hexdigest()[:32] + '"')
    headers = {"Cache-Control": "no-cache", "ETag": _INDEX_CACHE["etag"], "Vary": "Accept-Encoding", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == _INDEX_CACHE["etag"]:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(_INDEX_CACHE["gz"], media_type="text/html; charset=utf-8", headers={**headers, "Content-Encoding": "gzip"})
    return Response(_INDEX_CACHE["raw"], media_type="text/html; charset=utf-8", headers=headers)


STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
                ".woff2": "font/woff2", ".glb": "model/gltf-binary"}
_STATIC_CACHE: dict = {}


def _static_response(fp: Path, request: Request) -> Response:
    """Frontend assets (dist/vendor/…): explicit MIME types (Windows registries map .js/.mjs inconsistently, which breaks
    module loading), gzip for text, ETag revalidation."""
    st = fp.stat()
    key = (str(fp), st.st_mtime_ns, st.st_size)
    ent = _STATIC_CACHE.get(str(fp))
    if not ent or ent["key"] != key:
        raw = fp.read_bytes()
        ctype = STATIC_TYPES.get(fp.suffix.lower(), "application/octet-stream")
        text = ctype.startswith(("text/", "application/json", "image/svg"))
        ent = {"key": key, "raw": raw, "gz": gzip.compress(raw, 6, mtime=0) if text and len(raw) > 1024 else None, "ctype": ctype,
               "etag": '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'}
        _STATIC_CACHE[str(fp)] = ent
    headers = {"Cache-Control": "no-cache", "ETag": ent["etag"], "Vary": "Accept-Encoding", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == ent["etag"]:
        return Response(status_code=304, headers=headers)
    if ent["gz"] is not None and "gzip" in request.headers.get("accept-encoding", ""):
        return Response(ent["gz"], media_type=ent["ctype"], headers={**headers, "Content-Encoding": "gzip"})
    return Response(ent["raw"], media_type=ent["ctype"], headers=headers)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "No such endpoint."})
    index = WEB_DIST / "index.html"
    if path:
        fp = (WEB_DIST / path).resolve()
        if fp.is_file() and WEB_DIST.resolve() in fp.parents:
            return _static_response(fp, request)
        if path.startswith("vendor/"):
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "No such file."})
    if index.is_file():
        return _index_response(index, request)
    return JSONResponse({"error": {"code": "NO_FRONTEND", "message": "Frontend not built. Run: python apps/web/build.py"}}, status_code=503)
