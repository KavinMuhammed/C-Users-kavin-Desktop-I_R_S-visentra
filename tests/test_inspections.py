"""Phase 10: persistent inspections — database, storage, API, authorisation, idempotency, concurrency, recovery.

Every image is generated inside the test. The database is the test suite's own temporary SQLite file
(conftest sets VISENTRA_DATA_DIR to a fresh temp dir); no production or development database is touched.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from conftest import Api
from visentra_api import errors as E
from visentra_api.config import settings
from visentra_api.db import SessionLocal, engine
from visentra_api.inspection import lifecycle as L
from visentra_api.inspection import objects as OBJ
from visentra_api.inspection import service as SVC
from visentra_api.main import app
from visentra_api.models import (AuditEvent, ExperimentalRun, IdempotencyKey, Inspection, InspectionMedia, MediaRendition, StorageObject, now)
from visentra_api.security import limiter
from visentra_api.storage import storage

REPO = Path(__file__).resolve().parents[1]
URL = "/api/v1/inspections"


# ------------------------------------------------------------------ helpers
def jpeg(w=640, h=480, seed=1, exif=None, fmt="JPEG", mode="RGB") -> bytes:
    img = Image.new(mode, (w, h), (90 + seed % 100, 100, 110) if mode == "RGB" else (90, 100, 110, 200))
    for i in range(0, w, 37):                      # some structure so each seed differs
        img.putpixel((i % w, (i * seed) % h), (255, 255, 255) if mode == "RGB" else (255, 255, 255, 255))
    buf = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def exif_with_gps() -> bytes:
    ex = Image.Exif()
    ex[0x010F] = "TestMake"
    ex[0x0110] = "TestModel 1"
    ex[0x0132] = "2026:09:30 10:11:12"
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (51.0, 28.0, 38.0), "W", (0.0, 0.0, 1.0)
    return ex.tobytes()


def err(r):
    return r.json()["error"]


def create(api: Api, data: bytes | None = None, name="panel.jpg", key: str | None = None, **fields):
    form = {"component": "fuselage", **{k: v for k, v in fields.items() if v is not None}}
    h = {"Idempotency-Key": key} if key else None
    return api.post(URL, files={"file": (name, data if data is not None else jpeg(seed=uuid.uuid4().int % 997), "image/jpeg")}, data=form, headers=h)


def user(api: Api, prefix="insp") -> dict:
    return api.register(f"{prefix}-{uuid.uuid4().hex[:10]}@example.org")


def ws_id(api: Api) -> str:
    return next(w["id"] for w in api.get("/api/workspaces").json() if w.get("personal"))


def member(owner: Api, other_email: str, role: str, ws: str) -> None:
    r = owner.post(f"/api/workspaces/{ws}/members", json={"email": other_email, "role": role})
    assert r.status_code == 201, r.text


def counts(*tables) -> dict:
    with SessionLocal() as db:
        return {t: db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables}


def obj_path(key: str) -> Path:
    return storage._path(key)


@pytest.fixture()
def fresh():
    """A signed-in user in a new personal workspace."""
    limiter.reset()
    with TestClient(app) as c:
        a = Api(c)
        a.me = user(a)
        yield a


def second():
    c = TestClient(app)
    c.__enter__()
    a = Api(c)
    a.me = user(a, "other")
    return a


# ================================================================== schema & lifecycle
def test_phase10_tables_and_constraints_exist():
    from sqlalchemy import inspect
    ins = inspect(engine)
    names = set(ins.get_table_names())
    assert {"inspections", "inspection_ref_counters", "inspection_media", "media_renditions", "storage_objects", "experimental_runs", "experimental_predictions"} <= names
    uq = {tuple(u["column_names"]) for u in ins.get_unique_constraints("inspections")}
    assert ("workspace_id", "ref") in uq
    fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in ins.get_foreign_keys("inspection_media")}
    assert (("inspection_id",), "inspections") in fks and (("workspace_id",), "workspaces") in fks
    assert "request_hash" in {c["name"] for c in ins.get_columns("idempotency_keys")}
    with engine.connect() as c:
        # Later additive revisions preserve the Phase 10 constraints checked above.
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() in {"5b1e0c7a2d94", "c4e1a9d2b7f3", "e6201020a001"}


def test_lifecycle_matches_the_catalog_state_machine():
    cat = json.loads((REPO / "docs/architecture/architecture_catalog.json").read_text())["state_machines"]["inspections.status"]
    assert set(L.STATES) == set(cat["states"]) and L.INITIAL in cat["initial"]
    assert L.TRANSITIONS == {(a, b) for a, b, _ in cat["transitions"]}
    assert L.creation_path("none") == ["created", "awaiting_review"] and L.creation_path("model") == ["created", "analysing"]


def test_database_rejects_invalid_rows(fresh):
    r = create(fresh)
    iid = r.json()["id"]
    bad = [
        "UPDATE inspections SET status = 'approved' WHERE id = :i",                                  # not a lifecycle state
        "UPDATE inspections SET status = 'reviewed' WHERE id = :i",                                  # reviewed without sign-off fields
        "UPDATE inspections SET status = 'analysis_failed' WHERE id = :i",                           # failure without error code
        "UPDATE inspections SET ref = 'INS-1' WHERE id = :i",                                        # malformed reference
        "UPDATE inspections SET deleted_at = CURRENT_TIMESTAMP WHERE id = :i",                       # deleted without who
        "UPDATE inspections SET component = 'rotor' WHERE id = :i",
        "UPDATE inspections SET workspace_id = 'nope' WHERE id = :i",                                # FK
        "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) "
        "SELECT 'm-dup', workspace_id, inspection_id, 1, 'image', 'x', 'image/jpeg', 1, 1, 1, sha256, 'k/other', uploaded_by FROM inspection_media WHERE inspection_id = :i",  # ordinal unique
        "INSERT INTO storage_objects (object_key, store, purpose, state) VALUES ('k/x', 'objects', 'media_original', 'committed')",  # committed without checksum
        "INSERT INTO experimental_runs (id, user_id, source, model_id, experimental, operational_use, status, outcome, prediction_count, settings, timing_ms, created_at) "
        "SELECT 'r-op', created_by, 'upload', 'VT-DET-0001', TRUE, TRUE, 'completed', 'detections', 0, '{}', '{}', CURRENT_TIMESTAMP FROM inspections WHERE id = :i",  # operational use
    ]
    for sql in bad:
        with engine.begin() as c:
            with pytest.raises(IntegrityError):
                c.execute(text(sql), {"i": iid})
    with SessionLocal() as db:
        i = db.get(Inspection, iid)
        assert i.status == "awaiting_review" and i.ref.startswith("INS-") and i.deleted_at is None


def test_lifecycle_transitions_are_validated_and_never_overwrite_stale_state(fresh):
    iid = create(fresh).json()["id"]
    with SessionLocal() as db:
        with pytest.raises(Exception) as ex:                       # awaiting_review -> reviewed is not a transition
            L.transition(db, iid, "reviewed", expected_revision=0)
        assert ex.value.status_code == 409 and ex.value.detail["code"] == E.CONFLICT
        assert L.transition(db, iid, "in_review", expected_revision=0) == 1
        db.commit()
        with pytest.raises(Exception) as ex:                       # a writer that still holds revision 0
            L.transition(db, iid, "analysing", expected_revision=0)
        assert ex.value.detail["code"] == E.REVIEW_CONFLICT and ex.value.detail["details"]["current_revision"] == 1
        with pytest.raises(Exception) as ex:
            L.transition(db, "f" * 32, "in_review")
        assert ex.value.status_code == 404
        db.rollback()
        i = db.get(Inspection, iid)
        assert (i.status, i.revision) == ("in_review", 1)
        with pytest.raises(ValueError):
            L.update_fields(db, iid, 1, {"status": "reviewed"})


# ================================================================== create & retrieve
def test_create_returns_a_persisted_record_and_retrieve_matches(fresh):
    data = jpeg(800, 600, seed=7)
    r = create(fresh, data, name="wing panel.jpg", zone="STA 540", aircraft_ref="Training airframe 2", notes="raking light")
    assert r.status_code == 201, r.text
    b = r.json()
    year = date.today().year
    assert b["ref"] == f"INS-{year}-000001" and b["status"] == "awaiting_review" and b["analysis_mode"] == "none" and b["revision"] == 0
    assert r.headers["ETag"] == '"0"' and r.headers["Location"].endswith(b["id"])
    assert b["predictions"] == [] and b["human_findings"] == [] and b["analysis"] is None and b["origin"] == "server"
    # Phase 11: review and reports are stored on the server too (Phase 10 returned review/reports False here)
    assert b["persistence"] == {"inspection": True, "review": True, "reports": True, "note": b["persistence"]["note"]}
    m = b["media"][0]
    assert m["sha256"] == hashlib.sha256(data).hexdigest() and m["size_bytes"] == len(data) and (m["width"], m["height"]) == (800, 600)
    assert m["filename"] == "wing panel.jpg" and m["mime"] == "image/jpeg" and m["integrity"] == "ok"
    assert set(m["urls"]) == {"display", "thumb", "original"} and all(u.startswith("/api/media/inspection/") for u in m["urls"].values())
    g = fresh.get(f"{URL}/{b['id']}")
    assert g.status_code == 200 and g.json()["ref"] == b["ref"] and g.json()["media"] == b["media"] and g.headers["ETag"] == '"0"'
    s = fresh.get(f"{URL}/{b['id']}/status").json()
    assert s == {"id": b["id"], "status": "awaiting_review", "revision": 0, "analysis_mode": "none", "analysis": None, "updated_at": s["updated_at"]}
    # nothing internal leaks: no object key, no storage path
    with SessionLocal() as db:
        key = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.inspection_id == b["id"]))
    for txt in (r.text, g.text):
        assert key not in txt and str(settings.data_dir) not in txt and "object_key" not in txt
    ev = SessionLocal().query(AuditEvent).filter(AuditEvent.action == "inspection.create", AuditEvent.resource_id == b["id"]).one()
    assert ev.meta["status_path"] == ["created", "awaiting_review"] and ev.meta["analysis_requested"] is True


def test_original_is_preserved_byte_for_byte_and_renditions_carry_no_metadata(fresh):
    data = jpeg(1200, 900, seed=3, exif=exif_with_gps())
    b = create(fresh, data).json()
    m = b["media"][0]
    assert m["capture"] == {"has_gps": True, "camera_make": "TestMake", "camera_model": "TestModel 1", "taken_at": "2026-09-30T10:11:12"}
    assert "51" not in json.dumps(m["capture"])                 # coordinates are never stored
    o = fresh.get(m["urls"]["original"])
    assert o.status_code == 200 and o.content == data and o.headers["content-type"] == "image/jpeg"
    assert o.headers["Digest"].startswith("sha-256=") and o.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in o.headers["Content-Security-Policy"] and o.headers["Cache-Control"].startswith("private")
    assert o.headers["Content-Disposition"].startswith(f'attachment; filename="{b["ref"]}-')
    for v, side in (("display", 2048), ("thumb", 320)):
        x = fresh.get(m["urls"][v])
        assert x.status_code == 200 and x.headers["content-type"] == "image/jpeg"
        im = Image.open(io.BytesIO(x.content))
        assert max(im.size) <= side and not im.getexif() and "exif" not in im.info
    with SessionLocal() as db:
        objs = db.scalars(select(StorageObject).where(StorageObject.owner_id.in_(
            [m["id"]] + list(db.scalars(select(MediaRendition.id).where(MediaRendition.media_id == m["id"])))))).all()
        assert sorted(o.purpose for o in objs) == ["media_original", "media_rendition", "media_rendition"]
        assert all(o.state == "committed" and o.sha256 and o.size_bytes and o.committed_at for o in objs)
        assert all(hashlib.sha256(obj_path(o.object_key).read_bytes()).hexdigest() == o.sha256 for o in objs)


def test_exif_orientation_is_applied_to_dimensions_and_renditions(fresh):
    ex = Image.Exif()
    ex[0x0112] = 6                                                # rotate 90°: stored 800x400, shown 400x800
    b = create(fresh, jpeg(800, 400, seed=11, exif=ex.tobytes())).json()
    m = b["media"][0]
    assert (m["width"], m["height"]) == (400, 800)
    d = Image.open(io.BytesIO(fresh.get(m["urls"]["display"]).content))
    assert d.size == (400, 800)


def test_hostile_filenames_never_reach_storage_keys(fresh):
    for name in ("../../etc/passwd.jpg", "..\\..\\win.ini.jpg", "a/b/c.jpg", "<script>x</script>.jpg", "con.jpg\x00.exe"):
        r = create(fresh, name=name)
        assert r.status_code == 201, (name, r.text)
        m = r.json()["media"][0]
        assert "/" not in m["filename"] and "\\" not in m["filename"] and "<" not in m["filename"] and "\x00" not in m["filename"]
        with SessionLocal() as db:
            key = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.id == m["id"]))
        assert key.startswith("inspections/") and ".." not in key and "passwd" not in key and "script" not in key
        assert obj_path(key).resolve().is_relative_to(Path(settings.data_dir, "objects").resolve())


@pytest.mark.parametrize("case,status,code,reason", [
    ("ico", 415, "UNSUPPORTED_MEDIA_TYPE", None),            # Phase 13: GIF/BMP/TIFF/HEIC/AVIF are accepted; ICO is not
    ("text", 415, "UNSUPPORTED_MEDIA_TYPE", None),
    ("empty", 422, "MEDIA_INVALID", "empty"),
    ("small", 422, "MEDIA_INVALID", "too_small"),
    ("long", 422, "MEDIA_INVALID", "too_large_side"),
    ("truncated", 422, "MEDIA_INVALID", "corrupted"),
])
def test_invalid_uploads_are_rejected_and_nothing_is_stored(fresh, case, status, code, reason):
    good = jpeg(700, 500, seed=5)
    data = {"ico": jpeg(64, 64, fmt="ICO", mode="RGB"), "text": b"not an image at all, just text" * 10, "empty": b"",
            "small": jpeg(63, 600), "long": jpeg(12_001, 320), "truncated": good[: len(good) // 2]}[case]
    before = counts("inspections", "inspection_media", "storage_objects")
    files_before = sum(1 for _ in Path(settings.data_dir, "objects").rglob("*")) if Path(settings.data_dir, "objects").exists() else 0
    r = create(fresh, data, name="x.jpg")
    assert r.status_code == status, r.text
    assert err(r)["code"] == code and (reason is None or err(r)["details"]["reason"] == reason)
    assert counts("inspections", "inspection_media", "storage_objects") == before
    assert sum(1 for _ in Path(settings.data_dir, "objects").rglob("*")) == files_before


def test_size_and_pixel_limits(fresh, monkeypatch):
    monkeypatch.setattr(settings, "inspection_max_upload_mb", 1)
    big = os.urandom(1024 * 1024 + 10)
    r = create(fresh, big)
    assert r.status_code == 413 and err(r)["code"] == "TOO_LARGE"
    monkeypatch.setattr(settings, "inspection_max_upload_mb", 5)
    monkeypatch.setattr(settings, "max_image_pixels", 300_000)
    r = create(fresh, jpeg(800, 600))
    assert r.status_code == 422 and err(r)["details"]["reason"] == "too_many_pixels"


def test_field_validation(fresh):
    for form, field in (({"component": ""}, "component"), ({"component": "rotor"}, "component"), ({"notes": "x" * 1001}, "notes"),
                        ({"zone": "z" * 61}, "zone"), ({"aircraft_ref": "a" * 61}, "aircraft_ref"), ({"project_id": "nope"}, "project_id")):
        r = fresh.post(URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}, data={"component": "fuselage", **form})
        assert r.status_code == 422 and err(r)["code"] == "VALIDATION" and err(r)["field"] == field, (form, r.text)


def test_csrf_and_authentication_are_required(fresh):
    c = TestClient(app)
    assert c.get(URL).status_code == 401
    r = fresh.c.post(URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}, data={"component": "fuselage"})   # no X-CSRF-Token
    assert r.status_code == 403 and err(r)["code"] == "CSRF"


# ================================================================== idempotency & duplicates
def test_idempotent_create_replays_and_conflicts(fresh):
    data, key = jpeg(seed=21), f"k-{uuid.uuid4().hex}"
    a = create(fresh, data, key=key)
    b = create(fresh, data, key=key)
    assert a.status_code == b.status_code == 201 and b.headers.get("Idempotent-Replay") == "true" and a.json()["id"] == b.json()["id"]
    assert fresh.get(URL).json()["total"] == 1
    c = create(fresh, jpeg(seed=22), key=key)                          # same key, different image
    assert c.status_code == 409 and err(c)["code"] == "IDEMPOTENCY_CONFLICT"
    d = create(fresh, data, key=key, zone="different")                 # same key, different fields
    assert d.status_code == 409
    with SessionLocal() as db:
        row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        assert row.request_hash and len(row.request_hash) == 64


def test_reinspection_of_the_same_image_is_allowed_and_flagged(fresh):
    data = jpeg(seed=31)
    first = create(fresh, data, name="a.jpg").json()
    again = create(fresh, data, name="a.jpg")                           # no key: an intentional second inspection
    assert again.status_code == 201 and again.json()["id"] != first["id"] and again.json()["ref"] != first["ref"]
    w = again.json()["warnings"]
    assert w and w[0]["code"] == "DUPLICATE_MEDIA" and w[0]["inspection_ids"] == [first["id"]] and w[0]["refs"] == [first["ref"]]
    other = create(fresh, jpeg(seed=32), name="a.jpg")                  # same file name, different bytes: not a duplicate
    assert other.status_code == 201 and other.json()["warnings"] == []
    assert fresh.get(f"{URL}/{first['id']}").json()["warnings"][0]["inspection_ids"] == [again.json()["id"]]


# ================================================================== concurrency
def test_concurrent_creates_get_unique_contiguous_references(fresh):
    n = 8
    with ThreadPoolExecutor(n) as ex:
        rs = list(ex.map(lambda i: create(fresh, jpeg(seed=100 + i)), range(n)))
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs if r.status_code != 201]
    nums = sorted(int(r.json()["ref"].rsplit("-", 1)[1]) for r in rs)
    assert nums == list(range(1, n + 1))


def test_concurrent_duplicates_with_one_key_create_one_inspection(fresh):
    key, data = f"k-{uuid.uuid4().hex}", jpeg(seed=41)
    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: create(fresh, data, key=key), range(4)))
    assert {r.status_code for r in rs} == {201}
    assert len({r.json()["id"] for r in rs}) == 1
    assert fresh.get(URL).json()["total"] == 1
    with SessionLocal() as db:   # losers' objects were compensated: only the winner's three objects are committed
        assert db.scalar(select(text("COUNT(*)")).select_from(StorageObject).where(StorageObject.state == "pending")) == 0


def test_concurrent_updates_one_wins_the_rest_conflict(fresh):
    iid = create(fresh).json()["id"]
    with ThreadPoolExecutor(5) as ex:
        rs = list(ex.map(lambda i: fresh.patch(f"{URL}/{iid}", json={"notes": f"writer {i}"}, headers={"If-Match": '"0"'}), range(5)))
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409, 409]
    loser = next(r for r in rs if r.status_code == 409)
    assert err(loser)["code"] == "REVIEW_CONFLICT" and err(loser)["details"]["current_revision"] == 1
    winner = next(r for r in rs if r.status_code == 200).json()
    assert fresh.get(f"{URL}/{iid}").json()["notes"] == winner["notes"]


# ================================================================== update & delete
def test_patch_requires_the_current_revision(fresh):
    iid = create(fresh).json()["id"]
    r = fresh.patch(f"{URL}/{iid}", json={"notes": "a"})
    assert r.status_code == 428 and err(r)["code"] == "REVISION_REQUIRED"
    r = fresh.patch(f"{URL}/{iid}", json={"notes": "a", "zone": "LH wing"}, headers={"If-Match": "0"})
    assert r.status_code == 200 and r.json()["revision"] == 1 and r.headers["ETag"] == '"1"' and r.json()["zone"] == "LH wing"
    r = fresh.patch(f"{URL}/{iid}", json={"notes": "b"}, headers={"If-Match": "0"})
    assert r.status_code == 409 and err(r)["code"] == "REVIEW_CONFLICT"
    for body in ({"status": "reviewed"}, {"revision": 9}, {"ref": "INS-1"}, {"component": "rotor"}, {"notes": 5}, {}):
        r = fresh.patch(f"{URL}/{iid}", json=body, headers={"If-Match": "1"})
        assert r.status_code == 422, body
    assert fresh.get(f"{URL}/{iid}").json()["status"] == "awaiting_review"


def test_soft_delete_hides_the_record_but_keeps_evidence(fresh):
    b = create(fresh).json()
    r = fresh.delete(f"{URL}/{b['id']}")
    assert r.status_code == 204
    assert fresh.get(f"{URL}/{b['id']}").status_code == 404 and fresh.get(URL).json()["total"] == 0
    assert fresh.get(b["media"][0]["urls"]["display"]).status_code == 404
    assert fresh.delete(f"{URL}/{b['id']}").status_code == 204            # naturally idempotent
    with SessionLocal() as db:
        i = db.get(Inspection, b["id"])
        assert i.deleted_at is not None and i.deleted_by
        key = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.inspection_id == i.id))
    assert storage.exists(key)                                              # evidence kept until a retention decision


# ================================================================== record-level authorisation & roles
def test_other_workspaces_cannot_see_or_change_records(fresh):
    b = create(fresh).json()
    o = second()
    try:
        for r in (o.get(f"{URL}/{b['id']}"), o.get(f"{URL}/{b['id']}/status"), o.patch(f"{URL}/{b['id']}", json={"notes": "x"}, headers={"If-Match": "0"}),
                  o.delete(f"{URL}/{b['id']}")):
            assert r.status_code == 404 and err(r)["code"] == "INSPECTION_NOT_FOUND"
        for v in ("display", "thumb", "original"):
            assert o.get(b["media"][0]["urls"].get(v, f"/api/media/inspection/{b['media'][0]['id']}/{v}")).status_code == 404
        assert o.get(URL).json()["total"] == 0
        assert o.get(URL, params={"workspace_id": ws_id(fresh)}).status_code == 404
        r = o.post(URL, files={"file": ("a.jpg", jpeg(), "image/jpeg")}, data={"component": "fuselage", "workspace_id": ws_id(fresh)})
        assert r.status_code == 404 and err(r)["code"] == "WORKSPACE_NOT_FOUND"
    finally:
        o.c.__exit__(None, None, None)


@pytest.mark.parametrize("role,read_original,create_ok,update_ok,delete_ok", [
    ("VIEWER", False, False, False, False),
    ("REVIEWER", True, False, False, False),
    ("OPERATOR", True, True, True, False),
    ("ML_ENGINEER", True, True, True, False),
    ("ADMIN", True, True, True, True),
])
def test_workspace_roles_are_enforced_server_side(fresh, role, read_original, create_ok, update_ok, delete_ok):
    ws = fresh.post("/api/workspaces", json={"name": f"Hangar {role}"}).json()["id"]      # personal workspaces cannot be shared
    b = create(fresh, workspace_id=ws).json()
    assert b["workspace"]["id"] == ws
    o = second()
    try:
        member(fresh, o.me["user"]["email"] if "user" in o.me else o.me["email"], role, ws)
        g = o.get(f"{URL}/{b['id']}")
        assert g.status_code == 200
        assert ("original" in g.json()["media"][0]["urls"]) is read_original
        assert set(g.json()["allowed_actions"]) == {a for a, ok in (("update", update_ok), ("delete", delete_ok), ("read_original", read_original)) if ok}
        assert o.get(f"/api/media/inspection/{b['media'][0]['id']}/display").status_code == 200
        assert o.get(f"/api/media/inspection/{b['media'][0]['id']}/original").status_code == (200 if read_original else 403)
        assert o.get(URL, params={"workspace_id": ws}).json()["total"] == 1
        r = o.post(URL, files={"file": ("a.jpg", jpeg(seed=77), "image/jpeg")}, data={"component": "nacelle", "workspace_id": ws})
        assert r.status_code == (201 if create_ok else 403), r.text
        r = o.patch(f"{URL}/{b['id']}", json={"notes": "role"}, headers={"If-Match": "0"})
        assert r.status_code == (200 if update_ok else 403)
        r = o.delete(f"{URL}/{b['id']}")
        assert r.status_code == (204 if delete_ok else 403)
    finally:
        o.c.__exit__(None, None, None)


# ================================================================== history: pagination, search, filters, sorting
def test_history_pagination_search_filters_and_sorting(fresh):
    specs = [("fuselage", "STA 100", "Airframe A"), ("nacelle", "Inlet lip", "Airframe B"), ("fuselage", "STA 200", "Airframe A"),
             ("wing_upper", "Rib 7", "Airframe C"), ("fuselage", "STA 300 50%_off", "Airframe B")]
    ids = [create(fresh, component=c, zone=z, aircraft_ref=a).json()["id"] for c, z, a in specs]
    lst = fresh.get(URL, params={"per_page": 2}).json()
    assert (lst["total"], lst["pages"], lst["page"], lst["per_page"], len(lst["items"])) == (5, 3, 1, 2, 2)
    pages = [fresh.get(URL, params={"per_page": 2, "page": p}).json()["items"] for p in (1, 2, 3)]
    seen = [i["id"] for p in pages for i in p]
    assert seen == list(reversed(ids)) and len(set(seen)) == 5                        # newest first, no gaps or repeats
    assert all(i["thumb_url"].startswith("/api/media/inspection/") and i["counts"]["media"] == 1 for i in pages[0])
    assert fresh.get(URL, params={"q": "airframe b"}).json()["total"] == 2
    assert fresh.get(URL, params={"q": "50%_"}).json()["total"] == 1                     # LIKE wildcards are literal
    assert fresh.get(URL, params={"q": "%"}).json()["total"] == 1
    assert fresh.get(URL, params={"component": "fuselage"}).json()["total"] == 3
    assert fresh.get(URL, params={"status": "awaiting_review"}).json()["total"] == 5
    assert fresh.get(URL, params={"status": "reviewed"}).json()["total"] == 0
    today, tomorrow = date.today(), date.today() + timedelta(days=1)
    assert fresh.get(URL, params={"from": today.isoformat(), "to": today.isoformat()}).json()["total"] == 5
    assert fresh.get(URL, params={"from": tomorrow.isoformat()}).json()["total"] == 0
    cat = fresh.get(URL, params={"category": "dent"}).json()
    assert cat["total"] == 0          # Phase 11: a real filter on confirmed findings (none here); Phase 10 also returned an explanation note
    refs = [i["ref"] for i in fresh.get(URL, params={"sort": "ref", "dir": "asc"}).json()["items"]]
    assert refs == sorted(refs)
    comps = [i["component"] for i in fresh.get(URL, params={"sort": "component", "dir": "desc"}).json()["items"]]
    assert comps == sorted(comps, reverse=True)
    assert fresh.get(URL, params={"mine": "true"}).json()["total"] == 5
    for params, field in (({"status": "approved"}, "status"), ({"component": "x"}, "component"), ({"category": "scratch"}, "category"),
                          ({"sort": "password"}, "sort"), ({"dir": "up"}, "dir"), ({"from": "2026-13-01"}, "from"),
                          ({"from": tomorrow.isoformat(), "to": today.isoformat()}, "from"), ({"per_page": 101}, "per_page"),
                          ({"page": 0}, "page"), ({"q": "x" * 101}, "q")):
        r = fresh.get(URL, params=params)
        assert r.status_code == 422 and err(r)["code"] == "VALIDATION", (params, r.text)


# ================================================================== integrity & storage failures
def test_tampered_or_missing_objects_are_never_served(fresh):
    b = create(fresh, jpeg(seed=51)).json()
    m = b["media"][0]
    with SessionLocal() as db:
        okey = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.id == m["id"]))
        tkey = db.scalar(select(MediaRendition.object_key).where(MediaRendition.media_id == m["id"], MediaRendition.kind == "thumb"))
    p = obj_path(okey)
    orig = p.read_bytes()
    p.write_bytes(orig[:-10] + b"0123456789")
    r = fresh.get(m["urls"]["original"])
    assert r.status_code == 409 and err(r)["code"] == "INTEGRITY_FAILED" and err(r)["details"]["reason"] == "checksum_mismatch"
    assert fresh.get(f"{URL}/{b['id']}").json()["media"][0]["integrity"] == "checksum_mismatch"
    p.write_bytes(orig)
    assert fresh.get(m["urls"]["original"]).status_code == 200                            # restored from backup: verified again
    assert fresh.get(f"{URL}/{b['id']}").json()["media"][0]["integrity"] == "ok"
    obj_path(tkey).unlink()
    r = fresh.get(m["urls"]["thumb"])
    assert r.status_code == 409 and err(r)["details"]["reason"] == "missing"
    with SessionLocal() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "media.integrity_failed", AuditEvent.resource_id == m["id"]).count() == 2


def test_storage_failure_saves_nothing_and_cleans_up(fresh, monkeypatch):
    real_put, calls = storage.put, []

    def flaky(key, data, ct="application/octet-stream"):
        calls.append(key)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_put(key, data, ct)
    monkeypatch.setattr(storage, "put", flaky)
    before = counts("inspections", "inspection_media")
    r = create(fresh)
    assert r.status_code == 503 and err(r)["code"] == "STORAGE_UNAVAILABLE" and "Nothing was saved" in err(r)["message"]
    assert counts("inspections", "inspection_media") == before
    assert not storage.exists(calls[0])
    with SessionLocal() as db:
        states = {k: db.get(StorageObject, k).state for k in calls}
    assert states[calls[0]] == "deleted"


def test_database_failure_after_upload_removes_the_objects(fresh, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database went away")
    monkeypatch.setattr(SVC, "next_ref", boom)
    before = counts("inspections", "inspection_media", "media_renditions")
    with SessionLocal() as db:
        pending_before = set(db.scalars(select(StorageObject.object_key)))
    with TestClient(app, raise_server_exceptions=False) as c:
        c.cookies.update(fresh.c.cookies)
        quiet = Api(c)
        quiet.csrf = fresh.csrf
        r = create(quiet)
    assert r.status_code == 500 and err(r)["code"] == "SERVER"
    assert counts("inspections", "inspection_media", "media_renditions") == before
    with SessionLocal() as db:
        new = db.scalars(select(StorageObject).where(StorageObject.object_key.notin_(pending_before))).all()
    assert len(new) == 3 and all(o.state == "deleted" and not storage.exists(o.object_key) for o in new)


def test_reconciler_resolves_orphans_missing_objects_and_temp_files(fresh):
    # 1. a crashed request: intent + object written, no rows (TX2 never ran)
    key = "inspections/zz00/" + uuid.uuid4().hex + ".jpg"
    with SessionLocal() as db:
        OBJ.register_pending(db, [(key, "media_original", "image/jpeg")], "test-crash")
        db.commit()
    storage.put(key, b"orphan bytes")
    with SessionLocal() as db:
        db.execute(update(StorageObject).where(StorageObject.object_key == key).values(created_at=now() - timedelta(hours=2)))
        db.commit()
    # 2. a committed object that disappeared from the store
    b = create(fresh).json()
    with SessionLocal() as db:
        okey = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.id == b["media"][0]["id"]))
    obj_path(okey).unlink()
    # 3. a temporary file left by a process that died mid-write
    tmp = Path(settings.data_dir, "objects", "inspections", "zz00", "x.jpg.dead.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"partial")
    os.utime(tmp, (0, 0))
    with SessionLocal() as db:                                       # dry run changes nothing
        rep = OBJ.reconcile(db, storage, grace_s=60, verify=True, dry_run=True)
        assert rep.orphans_deleted >= 1 and okey in rep.missing
    assert storage.exists(key) and tmp.exists()
    with SessionLocal() as db:
        rep = OBJ.reconcile(db, storage, grace_s=60, verify=True)
    assert rep.orphans_deleted >= 1 and okey in rep.missing and rep.temp_removed >= 1 and rep.errors == 0
    assert not storage.exists(key) and not tmp.exists()
    with SessionLocal() as db:
        assert db.get(StorageObject, key).state == "deleted" and db.get(StorageObject, okey).state == "missing"
        assert db.get(InspectionMedia, b["media"][0]["id"]).integrity == "missing"
    assert fresh.get(f"{URL}/{b['id']}").json()["media"][0]["integrity"] == "missing"     # surfaced, never silently fixed


def test_reconcile_cli_reports_without_internal_paths(fresh):
    env = {**os.environ, "PYTHONPATH": f"{REPO / 'apps/api'}{os.pathsep}{REPO / 'ml'}"}
    p = subprocess.run([sys.executable, str(REPO / "scripts/reconcile_storage.py"), "--dry-run", "--verify"], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["dry_run"] is True and "pending_seen" in out and str(settings.data_dir) not in p.stdout


# ================================================================== persistence across a restart
def test_records_survive_an_application_restart(fresh):
    b = create(fresh, jpeg(seed=61)).json()
    engine.dispose()                                                 # drop every pooled connection
    code = (
        "from visentra_api.db import SessionLocal\n"
        "from visentra_api.models import Inspection, InspectionMedia\n"
        "from visentra_api.storage import storage\n"
        "import hashlib, json, sys\n"
        "db = SessionLocal(); i = db.get(Inspection, sys.argv[1]); m = db.query(InspectionMedia).filter_by(inspection_id=i.id).one()\n"
        "print(json.dumps({'ref': i.ref, 'status': i.status, 'ok': hashlib.sha256(storage.get(m.object_key)).hexdigest() == m.sha256}))\n"
    )
    env = {**os.environ, "PYTHONPATH": f"{REPO / 'apps/api'}{os.pathsep}{REPO / 'ml'}"}
    p = subprocess.run([sys.executable, "-c", code, b["id"]], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == {"ref": b["ref"], "status": "awaiting_review", "ok": True}
    with TestClient(app) as c2:                                    # a new app lifespan with the same cookie
        c2.cookies.update(fresh.c.cookies)
        assert c2.get(f"{URL}/{b['id']}").json()["ref"] == b["ref"]


# ================================================================== capabilities
def test_capabilities_report_persistence_without_reviews_reports_or_a_model(fresh):
    c = fresh.get("/api/system/capabilities").json()["inspection"]
    # Phase 11 turned reviews and reports on (they now exist); persistence alone still never connects them in the frontend gate
    assert c["persistence"] is True and c["reviews"] is True and c["reports"] is True and c["detection_model"]["available"] is False
    assert c["policy"]["min_side"] == settings.inspection_min_side and c["policy"]["max_upload_mb"] == settings.inspection_max_upload_mb == 5



@pytest.mark.parametrize("fmt,mime_in,stored", [("GIF", "image/gif", "image/png"), ("BMP", "image/bmp", "image/png"), ("TIFF", "image/tiff", "image/png"),
                                               ("AVIF", "image/avif", "image/png"), ("WEBP", "image/webp", "image/webp")])
def test_phase13_more_image_formats_are_accepted(fresh, fmt, mime_in, stored):
    """Phase 13: any common still-image format. Non-web formats are kept as a lossless PNG of the first frame; the upload's
    own format, size and SHA-256 are recorded in capture.source."""
    data = jpeg(500, 380, seed=9, fmt=fmt, mode="RGB")
    r = create(fresh, data, name=f"panel.{fmt.lower()}")
    assert r.status_code == 201, r.text
    m = r.json()["media"][0]
    assert m["mime"] == stored and (m["width"], m["height"]) == (500, 380)
    orig = fresh.get(m["urls"]["original"])
    assert orig.status_code == 200 and hashlib.sha256(orig.content).hexdigest() == m["sha256"]
    if stored != mime_in:
        src = m["capture"]["source"]
        assert src["format"] == fmt and src["sha256"] == hashlib.sha256(data).hexdigest() and src["bytes"] == len(data)
        assert Image.open(io.BytesIO(orig.content)).size == (500, 380)


def test_phase13_heic_photos_are_accepted():
    pytest.importorskip("pillow_heif")
    from visentra_api.inspection import media as M
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (120, 130, 140)).save(buf, "HEIF")
    v = M.validate(buf.getvalue())
    assert v.fmt == "HEIF" and v.mime == "image/png" and v.capture["source"]["mime"] == "image/heic" and (v.width, v.height) == (400, 300)
