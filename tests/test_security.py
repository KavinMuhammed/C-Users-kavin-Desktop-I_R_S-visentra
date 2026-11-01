"""Security tests for the inspection product: auth, CSRF, IDOR across workspaces, upload validation, path traversal,
rate limiting, headers and the error contract (Phase 13 rewrite; the classification-workbench cases were retired with it)."""
from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from p11_fixtures import create, jpeg, user
from visentra_api.db import SessionLocal
from visentra_api.inspection.media import sanitize_filename
from visentra_api.models import User
from visentra_api.storage import LocalStorage

URL = "/api/v1/inspections"


def test_requires_auth(api):
    for url in (URL, "/api/audit-log", "/api/workspaces", "/api/system/capabilities", "/api/v1/detection-model", "/api/v1/inspection-analytics"):
        r = api.get(url)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED", url


def test_password_is_hashed_and_policy_enforced(api):
    r = api.c.post("/api/auth/register", json={"name": "Weak", "email": "weak@example.com", "password": "short"})
    assert r.status_code == 422
    api.register("hash@example.com", password="a-strong-passphrase-9")
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="hash@example.com").one()
        assert u.password_hash.startswith("scrypt$") and "a-strong-passphrase-9" not in u.password_hash


def test_login_errors_are_generic_and_rate_limited(api):
    api.register("rl@example.com")
    r = api.c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    codes = [api.c.post("/api/auth/login", json={"email": "rl@example.com", "password": "wrong"}).status_code for _ in range(10)]
    assert 429 in codes


def test_csrf_required_for_mutations(api):
    api.register("csrf@example.com")
    r = api.c.post(URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}, data={"component": "fuselage"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF"
    r = api.c.post(URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}, data={"component": "fuselage"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_other_users_cannot_see_or_change_an_inspection(api, api2):
    user(api, "idor-a")
    b = create(api, jpeg(seed=3))
    api2.register(f"idor-b-{uuid.uuid4().hex[:8]}@example.org")
    for r in (api2.get(f"{URL}/{b['id']}"), api2.get(f"/api/media/inspection/{b['media'][0]['id']}/original"),
              api2.patch(f"{URL}/{b['id']}", json={"zone": "x"}, headers={"If-Match": '"0"'}), api2.delete(f"{URL}/{b['id']}")):
        assert r.status_code == 404
    assert b["id"] not in {x["id"] for x in api2.get(URL).json()["items"]}


def test_upload_validation_never_trusts_the_client_type(api):
    user(api, "upl")
    for data, name in ((b"<script>alert(1)</script>", "x.jpg"), (b"MZ\x90\x00" + b"\x00" * 200, "a.png"), (jpeg()[:200], "cut.jpg")):
        r = api.post(URL, files={"file": (name, data, "image/jpeg")}, data={"component": "fuselage"})
        assert r.status_code in (415, 422), r.text
    big = io.BytesIO()
    Image.effect_noise((2400, 2400), 90).convert("RGB").save(big, "PNG")
    assert len(big.getvalue()) > 5 * 1024 * 1024
    r = api.post(URL, files={"file": ("big.png", big.getvalue(), "image/png")}, data={"component": "fuselage"})
    assert r.status_code == 413 and "5 MB" in r.json()["error"]["message"]


def test_filenames_are_sanitised():
    assert sanitize_filename("..\\..\\win.ini") == "win.ini" and sanitize_filename("") == "image"
    assert sanitize_filename("a/b/<x>.jpg") == "_x_.jpg"


def test_storage_rejects_traversal(tmp_path):
    st = LocalStorage(tmp_path)
    for bad in ("../x", "images/../../x", "/etc/passwd", "a/b/../../c", "A/B"):
        with pytest.raises(ValueError):
            st.put(bad, b"x")


def test_security_headers_error_contract_and_no_stack_traces(api):
    r = api.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY" and "Content-Security-Policy" in r.headers
    user(api, "hdr")
    r = api.get(f"{URL}/not-a-real-id", headers={"X-Request-ID": "client-trace-1234"})
    e = r.json()["error"]
    assert r.status_code == 404 and set(e) >= {"code", "message", "details", "request_id"} and "Traceback" not in r.text
    assert r.headers["X-Request-ID"] == "client-trace-1234"


def test_removed_workbench_endpoints_are_gone(api):
    user(api, "gone")
    for url in ("/api/projects", "/api/models", "/api/analytics", "/api/devices", "/api/notifications", "/api/search?q=x", "/api/events",
                "/api/v1/experimental/detection/status"):
        assert api.get(url).status_code == 404, url
