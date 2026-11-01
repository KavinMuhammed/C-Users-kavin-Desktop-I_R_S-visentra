"""Backend audit (27 Oct 2026): every API operation from the OpenAPI schema is exercised
  · anonymously — protected operations must refuse, nothing may 5xx;
  · as a user of another workspace against owner A's real objects — must never succeed, nothing may 5xx;
  · with malformed bodies as the owner — must be a 4xx validation error, never a 5xx.
All failures are collected and reported together, so one run shows the whole surface."""
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visentra_api.main import app
from visentra_api.security import limiter

PUBLIC = {("GET", "/api/health"), ("GET", "/api/health/live"), ("GET", "/api/health/ready"), ("POST", "/api/auth/register"),
          ("POST", "/api/auth/login"), ("POST", "/api/auth/forgot"), ("POST", "/api/auth/reset"), ("POST", "/api/auth/logout"),
          ("GET", "/api/system/version")}
# operations that would end the caller's own session or delete the owner's fixture; exercised separately
SESSION_ENDING = {("POST", "/api/auth/logout"), ("POST", "/api/auth/refresh")}
MALFORMED = [[], "text", None, 7, {"x": {"y": [1, {"z": None}]}}, {"component": ["a"], "notes": {"a": 1}, "force": "yes", "reason": 5}]


def _png(seed: int = 1) -> bytes:
    img = Image.new("RGB", (96, 96), (90 + seed, 100, 110))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _server_error(r) -> bool:
    """A 500, or any 5xx without the structured error body. A structured 503 (model switched off, store busy) is a
    correct answer, not a defect."""
    if r.status_code < 500:
        return False
    try:
        return r.status_code != 503 or "code" not in r.json()["error"]
    except Exception:
        return True


def _client():
    return TestClient(app, raise_server_exceptions=False)


def _register(c: TestClient, email: str) -> str:
    r = c.post("/api/auth/register", json={"name": "Audit User", "email": email, "password": "correct horse 42"})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def _operations():
    spec = app.openapi()
    return sorted((m.upper(), p) for p, ops in spec["paths"].items() for m in ops)


@pytest.fixture(scope="module")
def world():
    limiter.reset()
    with _client() as a, _client() as b, _client() as anon:
        ca, cb = _register(a, "audit-owner@example.org"), _register(b, "audit-other@example.org")
        r = a.post("/api/inspections", headers={"X-CSRF-Token": ca}, files={"file": ("a.png", _png(), "image/png")}, data={"component": "fuselage"})
        assert r.status_code == 201, r.text
        insp = r.json()
        ws = a.get("/api/auth/me").json()["workspaces"][0]["id"]
        ids = {"inspection_id": insp["id"], "media_id": insp["media"][0]["id"], "workspace_id": ws, "variant": "original",
               "user_id": a.get("/api/auth/me").json()["user"]["id"]}
        yield {"a": a, "b": b, "anon": anon, "ca": ca, "cb": cb, "ids": ids, "owned": set(ids) - {"variant"}}


def _url(path: str, ids: dict) -> str:
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: ids.get(m.group(1), "0" * 32), path)


def _send(c: TestClient, method: str, url: str, csrf: str | None, body=None, raw: bool = False):
    h = {"X-CSRF-Token": csrf} if csrf else {}
    if method in ("GET", "DELETE"):
        return c.request(method, url, headers=h)
    if raw:
        return c.request(method, url, headers={**h, "Content-Type": "application/json"}, content=body)
    return c.request(method, url, headers=h, json=body if body is not None else {})


def test_anonymous_requests_are_refused_and_never_fail(world):
    bad = []
    for method, path in _operations():
        r = _send(world["anon"], method, _url(path, world["ids"]), None)
        if _server_error(r):
            bad.append(f"{method} {path} -> {r.status_code}")
        elif (method, path) not in PUBLIC and r.status_code < 400:
            bad.append(f"{method} {path} -> {r.status_code} without a session")
    assert not bad, "\n".join(bad)


def test_other_workspace_cannot_reach_owner_objects(world):
    bad = []
    for method, path in _operations():
        if (method, path) in SESSION_ENDING:
            continue
        targets_owner = any("{" + k in path for k in world["owned"])
        r = _send(world["b"], method, _url(path, world["ids"]), world["cb"])
        if _server_error(r):
            bad.append(f"{method} {path} -> {r.status_code}")
        elif targets_owner and r.status_code < 400:
            bad.append(f"{method} {path} -> {r.status_code}: another workspace reached the owner's object")
        elif targets_owner and r.status_code == 403 and "{inspection_id}" in path:
            bad.append(f"{method} {path} -> 403 reveals that the inspection exists (expected 404)")
    # the owner's inspection is untouched
    r = world["a"].get(f"/api/inspections/{world['ids']['inspection_id']}")
    assert r.status_code == 200 and r.json()["revision"] == 0, r.text
    assert not bad, "\n".join(bad)


def test_malformed_bodies_are_validation_errors_not_server_errors(world):
    bad = []
    for method, path in _operations():
        if method not in ("POST", "PUT", "PATCH") or (method, path) in SESSION_ENDING:
            continue
        for body in MALFORMED:
            r = _send(world["a"], method, _url(path, world["ids"]), world["ca"], body)
            if _server_error(r):
                bad.append(f"{method} {path} body={body!r} -> {r.status_code} {r.text[:120]}")
        for raw in (b"{not json", b"\xff\xfe\x00", b'{"a":' * 50 + b"1" + b"}" * 50):
            r = _send(world["a"], method, _url(path, world["ids"]), world["ca"], raw, raw=True)
            if _server_error(r):
                bad.append(f"{method} {path} raw={raw[:12]!r} -> {r.status_code} {r.text[:120]}")
    assert not bad, "\n".join(bad)


def test_hostile_identifiers_and_headers_are_handled(world):
    bad = []
    hostile = ["..%2F..%2Fdata", "%00", "x" * 300, "' OR 1=1 --", "%E2%80%AE", "1e999"]
    for method, path in _operations():
        if "{" not in path or method != "GET":
            continue
        for h in hostile:
            url = re.sub(r"\{(\w+)(?::\w+)?\}", h, path)
            r = world["a"].get(url)
            if _server_error(r) or (r.status_code < 400 and path != "/{path:path}"):
                bad.append(f"GET {url[:80]} -> {r.status_code}")
    # a CSRF header that is not ASCII must be refused, not crash the comparison
    r = world["a"].patch("/api/auth/me", headers={"X-CSRF-Token": "é".encode("latin-1") * 10}, json={"name": "Renamed"})
    if r.status_code != 403:
        bad.append(f"non-ASCII CSRF header -> {r.status_code}")
    for page in ("0", "-1", "abc", "999999999999"):
        r = world["a"].get(f"/api/inspections?page={page}&per_page=1000")
        if _server_error(r) or r.status_code == 200:
            bad.append(f"list page={page} per_page=1000 -> {r.status_code}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("in_log", [False, True])
def test_password_reset_link_is_logged_only_when_allowed(world, monkeypatch, caplog, in_log):
    """A reset link is a bearer credential. Production default (PASSWORD_RESET_LINK_IN_LOG unset) keeps it out of the log."""
    from visentra_api.config import settings
    monkeypatch.setattr(settings, "reset_link_in_log", in_log)
    limiter.reset()
    with caplog.at_level("WARNING", logger="visentra.auth"):
        r = world["anon"].post("/api/auth/forgot", json={"email": "audit-owner@example.org"})
    assert r.status_code == 200
    text = "\n".join(rec.getMessage() for rec in caplog.records if rec.name == "visentra.auth")
    assert ("/#/reset/" in text) is in_log, text
    assert "password reset" in text


def test_liveness_answers_while_every_worker_thread_is_busy():
    """Analyses wait for the single detector slot inside worker threads. Liveness must not depend on those threads, or an
    orchestrator would restart a busy but healthy server in the middle of inspections."""
    import threading

    import anyio
    release = threading.Event()
    with TestClient(app) as c:
        lim = c.portal.call(anyio.to_thread.current_default_thread_limiter)
        old = lim.total_tokens
        lim.total_tokens = 1
        try:
            c.portal.start_task_soon(anyio.to_thread.run_sync, release.wait)
            for path in ("/api/health/live", "/api/health"):
                r = c.get(path)
                assert r.status_code == 200 and r.json()["status"] == "ok", (path, r.text)
        finally:
            release.set()
            lim.total_tokens = old


def test_queued_analyses_cannot_starve_other_endpoints(monkeypatch):
    """12 uploads stuck behind the detector use at most ANALYSIS_THREADS worker threads, so with a 9-thread pool the
    readiness check (a normal sync endpoint) still answers."""
    import threading

    import anyio
    from fastapi.responses import JSONResponse

    from visentra_api.inspection import service as SVC
    from visentra_api.routers import inspections as R
    release, started = threading.Event(), threading.Semaphore(0)

    def stuck_create(*a, **k):
        started.release()
        release.wait(30)
        return JSONResponse({"ok": True}, status_code=201)

    monkeypatch.setattr(SVC, "create", stuck_create)
    monkeypatch.setattr(R, "_analysis_limiter", None)
    limiter.reset()
    with TestClient(app) as c:
        csrf = _register(c, "audit-starve@example.org")
        lim = c.portal.call(anyio.to_thread.current_default_thread_limiter)
        old = lim.total_tokens
        lim.total_tokens = R.ANALYSIS_THREADS + 1
        posts = [threading.Thread(target=c.post, args=("/api/inspections",), daemon=True,
                                  kwargs={"headers": {"X-CSRF-Token": csrf}, "files": {"file": ("a.png", _png(), "image/png")}, "data": {"component": "fuselage"}})
                 for _ in range(12)]
        try:
            for t in posts:
                t.start()
            for _ in range(R.ANALYSIS_THREADS):
                assert started.acquire(timeout=10)
            got = {}
            t = threading.Thread(target=lambda: got.setdefault("r", c.get("/api/health/ready")), daemon=True)
            t.start(); t.join(10)
            assert "r" in got, "readiness did not answer while uploads were queued for the detector"
            assert got["r"].status_code in (200, 503)
        finally:
            release.set()
            for t in posts:
                t.join(10)
            lim.total_tokens = old


def test_s3_missing_object_is_reported_as_missing_not_as_an_outage():
    """S3Storage must raise FileNotFoundError for a missing key (like LocalStorage), so a deleted image is an integrity
    failure (409) rather than "store unavailable" (503); other S3 errors still propagate."""
    from botocore.exceptions import ClientError

    from visentra_api.storage import S3Storage

    class Stub:
        def __init__(self, code):
            self.code = code

        def get_object(self, **kw):
            raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "GetObject")

    s = S3Storage.__new__(S3Storage)
    s.bucket = "b"
    s.client = Stub("NoSuchKey")
    with pytest.raises(FileNotFoundError):
        s.get("inspections/ab/cd/x.jpg")
    s.client = Stub("AccessDenied")
    with pytest.raises(ClientError):
        s.get("inspections/ab/cd/x.jpg")


def test_uploads_never_reach_pillow_parsers_for_other_formats(monkeypatch):
    """pip-audit (27 Oct): Pillow's parsers for formats VisionTra does not accept (EPS, MSP, Spider, XBM, PPM …) had
    vulnerabilities. Uploads and OCR images must only be offered to the accepted openers."""
    from PIL import Image as PILImage

    from visentra_api.inspection import media, ocr
    assert set(media.open_formats()) <= set(media.FORMATS) and "PPM" not in media.open_formats() and "EPS" not in media.open_formats()
    buf = io.BytesIO()
    Image.new("RGB", (96, 96)).save(buf, "PPM")
    ppm = buf.getvalue()
    PILImage.init()
    factory, accept = PILImage.OPEN["PPM"]

    def tripwire(*a, **k):
        raise AssertionError("the PPM parser was offered untrusted upload bytes")
    monkeypatch.setitem(PILImage.OPEN, "PPM", (tripwire, accept))
    with pytest.raises(media.MediaRejected) as ex:
        media.validate(ppm)
    assert ex.value.status == 415
    with pytest.raises(ocr.ExtractionError) as ex2:
        ocr.validate(ppm)
    assert ex2.value.code == "OCR_INVALID_FILE"
    # every accepted upload format still opens (HEIF only when pillow-heif is installed)
    for fmt in ("JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"):
        b = io.BytesIO()
        Image.new("RGB", (96, 96), (120, 90, 60)).save(b, fmt)
        assert media.validate(b.getvalue()).fmt == fmt
    # an accepted-but-not-OCR image keeps its specific OCR code
    b = io.BytesIO()
    Image.new("RGB", (96, 96)).save(b, "GIF")
    with pytest.raises(ocr.ExtractionError) as ex3:
        ocr.validate(b.getvalue())
    assert ex3.value.code == "OCR_UNSUPPORTED"


@pytest.mark.parametrize("secret,ok", [("CHANGE_ME", False), ("x" * 20, False), ("change_me" + "y" * 40, False), ("s" * 48, True)])
def test_production_refuses_placeholder_or_short_session_secret(monkeypatch, tmp_path, secret, ok):
    """infra/staging/staging.env.example ships JWT_SECRET=CHANGE_ME: a copied template must not start in production."""
    from visentra_api import config
    monkeypatch.setattr(config, "_load_dotenv", lambda: None)
    monkeypatch.setenv("VISENTRA_ENV", "production")
    monkeypatch.setenv("VISENTRA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JWT_SECRET", secret)
    if ok:
        assert config.load_settings().is_production
    else:
        with pytest.raises(RuntimeError, match="at least 32 characters"):
            config.load_settings()


def test_startup_migration_keeps_json_request_logging():
    """migrate() runs at every non-test start-up. It must not let alembic.ini replace the root logger: request logs are
    INFO and must stay JSON with request ids (staging dry run, 27 Oct)."""
    import logging

    from visentra_api.logs import JsonFormatter
    from visentra_api.main import migrate
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    migrate()
    assert root.level == level and root.handlers == handlers, (logging.getLevelName(root.level), root.handlers)
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    assert not logging.getLogger("visentra.api").disabled


def test_request_log_lines_carry_the_request_id_and_user():
    """Every API request is logged as JSON with the same request id the client receives in X-Request-ID (staging dry run,
    27 Oct: the line used to be written after the id was cleared) and, when signed in, the user id."""
    import json as _json
    import logging

    from visentra_api.logs import JsonFormatter
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    lg = logging.getLogger("visentra.api")
    old = lg.level
    lg.addHandler(h); lg.setLevel(logging.INFO)
    try:
        limiter.reset()
        with _client() as c:
            csrf = _register(c, "audit-logline@example.org")
            r = c.get("/api/inspections", headers={"X-Request-ID": "audit-req-0001"})
            r2 = c.get("/api/health/live")
    finally:
        lg.removeHandler(h); lg.setLevel(old)
    lines = [_json.loads(x) for x in buf.getvalue().splitlines() if '"msg": "request"' in x]
    mine = [x for x in lines if x.get("path") == "/api/inspections"]
    assert mine and mine[-1].get("request_id") == "audit-req-0001" == r.headers["x-request-id"], mine
    assert mine[-1].get("user_id"), mine[-1]
    live = [x for x in lines if x.get("path") == "/api/health/live"]
    assert live and live[-1].get("request_id") == r2.headers["x-request-id"], live
