"""Focused recovery tests. All mutations use isolated temporary databases."""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from p11_fixtures import create, user, signoff
from visentra_api.config import resolve_database_url
from visentra_api.db import SessionLocal
from visentra_api.inspection import ocr
from visentra_api.models import AuditEvent, TextEvidence, WorkspaceMember

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/inspections"


def isolated_env(tmp):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MODEL_STORAGE_PATH")}
    env.update(VISENTRA_DATA_DIR=str(tmp / "data"), JWT_SECRET="phase1-test-" + "x" * 40,
               VISENTRA_ENV="test", PYTHONPATH=os.pathsep.join([str(ROOT / "apps/api"), str(ROOT / "ml")]),
               VISIONTRA_INSPECTION_MODEL="off", VISIONTRA_DOMAIN_GATE="off", PYTHONUTF8="1")
    return env


@pytest.mark.parametrize("url", ["", "sqlite:///data/visentra.db", "sqlite+pysqlite:///data/visentra.db"])
def test_config_same_database_across_launch_directories(tmp_path, url):
    env = isolated_env(tmp_path)
    env.pop("VISENTRA_DATA_DIR")
    if url:
        env["DATABASE_URL"] = url
    # An unrelated .env must not change the application configuration.
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///wrong.db\n")
    code = "import json; from visentra_api.config import settings; from visentra_api.db import engine; print(json.dumps([settings.database_url, str(engine.url), str(settings.data_dir)]))"
    results = []
    for cwd in (ROOT, ROOT / "apps/api", tmp_path):
        p = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
        assert p.returncode == 0, p.stderr
        results.append(json.loads(p.stdout))
    assert results[0] == results[1] == results[2]
    assert results[0][0] == results[0][1]
    assert Path(results[0][2]) == ROOT / "data"
    assert not (tmp_path / "wrong.db").exists()


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "sqlite:///file::memory:?uri=true",
                                "sqlite:///file:shared?mode=memory&cache=shared&uri=true",
                                "postgresql+psycopg://user:password@localhost/database"])
def test_nonfile_database_urls_unchanged(url):
    assert resolve_database_url(url) == url


def test_dotenv_comments_quotes_windows_paths(tmp_path, monkeypatch):
    import visentra_api.config as C
    monkeypatch.setattr(C, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text('PHASE1_VALUE="quoted # value" # comment\nPHASE1_PATH=C:\\some\\folder # comment\nPHASE1_NUMBER=7 # note\n')
    for name in ("PHASE1_VALUE", "PHASE1_PATH", "PHASE1_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    C._load_dotenv()
    assert os.environ["PHASE1_VALUE"] == "quoted # value"
    assert os.environ["PHASE1_PATH"] == "C:\\some\\folder"
    assert os.environ["PHASE1_NUMBER"] == "7"


def test_alembic_cli_and_application_share_database_and_startup(tmp_path):
    env = isolated_env(tmp_path)
    cli = subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "apps/api/alembic.ini"), "upgrade", "head"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert cli.returncode == 0, cli.stderr
    env["VISENTRA_ENV"] = "development"  # Exercise the real startup lifespan/migrate path.
    code = """
from fastapi.testclient import TestClient
from visentra_api.main import app
from visentra_api.db import engine
from sqlalchemy import text
with TestClient(app) as c:
    assert c.get('/api/health/live').status_code == 200
with engine.connect() as c:
    assert c.execute(text('select version_num from alembic_version')).scalar() == 'e6201020a001'
print('STARTUP_OK')
"""
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert p.returncode == 0, p.stderr
    assert "STARTUP_OK" in p.stdout
    assert (tmp_path / "data/visentra.db").exists()
    assert not (tmp_path / "visentra.db").exists()


def text_image():
    im = Image.new("RGB", (900, 180), "white")
    font = ImageFont.truetype("arial.ttf", 48) if os.name == "nt" else ImageFont.truetype("DejaVuSans.ttf", 48)
    ImageDraw.Draw(im).text((25, 50), "VT-ABC 12345", font=font, fill="black")
    return im


def image_bytes(im=None, fmt="PNG"):
    b = io.BytesIO()
    (im or text_image()).save(b, fmt)
    return b.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "PDF"])
def test_real_local_ocr_image_and_scanned_pdf(fmt):
    data = image_bytes(fmt=fmt)
    result = ocr.extract(data, ocr.validate(data))
    assert result["status"] == "completed"
    assert "VT-ABC 12345" in result["original_text"]
    assert result["pages"][0]["page"] == 1
    assert result["pages"][0]["method"] == "ocr"
    assert 0 <= result["pages"][0]["confidence"] <= 1


def selectable_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    w = PdfWriter()
    page = w.add_blank_page(400, 200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 20 100 Td (Verified selectable text) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    b = io.BytesIO(); w.write(b)
    return b.getvalue()


def test_selectable_pdf_does_not_run_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "_recognize", lambda *a: pytest.fail("Selectable text should not invoke OCR"))
    result = ocr.extract(selectable_pdf(), "application/pdf")
    assert result["original_text"] == "Verified selectable text"
    assert result["pages"][0]["confidence"] is None
    assert result["pages"][0]["method"] == "pdf_text"


def test_real_no_text_is_not_fabricated():
    result = ocr.extract(image_bytes(Image.new("RGB", (400, 200), "white")), "image/png")
    assert result["status"] == "no_text"
    assert result["original_text"] == ""


@pytest.mark.parametrize("case,code", [("invalid", "OCR_INVALID_FILE"), ("large", "OCR_TOO_LARGE"),
                                      ("encrypted", "OCR_ENCRYPTED"), ("pages", "OCR_PAGE_LIMIT"),
                                      ("animated", "OCR_UNSUPPORTED")])
def test_ocr_rejects_invalid_sources(case, code):
    from pypdf import PdfWriter
    if case == "invalid":
        data = b"not an image"
    elif case == "large":
        data = b"x" * (ocr.MAX_BYTES + 1)
    elif case == "animated":
        b = io.BytesIO()
        Image.new("RGB", (64, 64)).save(b, "TIFF", save_all=True, append_images=[Image.new("RGB", (64, 64), "white")])
        data = b.getvalue()
    else:
        w = PdfWriter()
        for _ in range(13 if case == "pages" else 1):
            w.add_blank_page(200, 200)
        if case == "encrypted":
            w.encrypt("test")
        b = io.BytesIO(); w.write(b); data = b.getvalue()
    with pytest.raises(ocr.ExtractionError) as ex:
        ocr.validate(data)
    assert ex.value.code == code


def test_text_persistence_search_correction_and_source(api):
    user(api, "ocr"); insp = create(api)
    url = f"{URL}/{insp['id']}/text-evidence"
    data = selectable_pdf()
    r = api.post(url, files={"file": ("manual.pdf", data, "application/pdf")}, data={"document_revision": "test-revision"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "completed"
    assert row["document_revision"] == "test-revision"
    assert api.get(row["source_url"]).content == data
    assert api.get(url, params={"q": "selectable"}).json()["items"][0]["id"] == row["id"]
    duplicate = api.post(url, files={"file": ("manual.pdf", data, "application/pdf")})
    assert duplicate.json()["id"] == row["id"]
    corrected = api.patch(f"{url}/{row['id']}", json={"text": "Corrected identifier", "confirmed": True}, headers={"If-Match": str(row["revision"])})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["original_text"] == "Verified selectable text"
    assert corrected.json()["confirmed_by"]
    assert api.patch(f"{url}/{row['id']}", json={"text": "stale", "confirmed": True}, headers={"If-Match": str(row["revision"])}).status_code == 409
    assert api.get(url, params={"q": "Corrected"}).json()["items"][0]["corrected_text"] == "Corrected identifier"
    with SessionLocal() as db:
        saved = db.get(TextEvidence, row["id"])
        assert saved.corrected_text == "Corrected identifier"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "text.confirm", AuditEvent.resource_id == insp["id"]))


def test_original_image_extraction_and_failure_retry(api, monkeypatch):
    user(api, "ocr-failure"); insp = create(api, data=image_bytes())
    url = f"{URL}/{insp['id']}/text-evidence"
    original = ocr.extract
    def fail(*a):
        raise ocr.ExtractionError("OCR_FAILED", "test-only injected engine failure")
    monkeypatch.setattr(ocr, "extract", fail)
    failed = api.post(url).json()
    assert failed["status"] == "failed" and failed["original_text"] == "" and failed["pages"] == []
    assert failed["error_code"] == "OCR_FAILED"
    assert api.get(failed["source_url"]).status_code == 200
    assert api.patch(f"{url}/{failed['id']}", json={"text": "invented", "confirmed": True},
                     headers={"If-Match": "1"}).status_code == 409
    monkeypatch.setattr(ocr, "extract", original)
    good = api.post(url).json()
    assert good["id"] == failed["id"] and good["revision"] == 2
    assert "VT-ABC 12345" in good["original_text"]
    assert good["error_code"] is None


def test_workspace_permissions_and_signed_off_immutability(api, api2):
    user(api, "ocr-owner"); insp = create(api)
    user(api2, "ocr-stranger")
    url = f"{URL}/{insp['id']}/text-evidence"
    row = api.post(url, files={"file": ("doc.pdf", selectable_pdf(), "application/pdf")}).json()
    for response in (api2.get(url), api2.post(url), api2.get(row["source_url"]),
                     api2.patch(f"{url}/{row['id']}", json={"text": "x", "confirmed": True}, headers={"If-Match": "1"})):
        assert response.status_code == 404
    assert signoff(api, insp["id"]).status_code == 200
    assert api.post(url).status_code == 409
    assert api.patch(f"{url}/{row['id']}", json={"text": "x", "confirmed": True}, headers={"If-Match": "1"}).status_code == 409
    assert api.get(url).status_code == 200


def test_viewer_cannot_extract_correct_or_download_original(api, api2):
    owner = user(api, "ocr-view-owner"); insp = create(api)
    viewer = user(api2, "ocr-viewer")
    url = f"{URL}/{insp['id']}/text-evidence"
    row = api.post(url, files={"file": ("doc.pdf", selectable_pdf(), "application/pdf")}).json()
    with SessionLocal() as db:
        db.add(WorkspaceMember(workspace_id=insp["workspace"]["id"], user_id=viewer["user"]["id"], role="VIEWER"))
        db.commit()
    assert api2.get(url).status_code == 200
    assert api2.post(url).status_code == 403
    assert api2.get(row["source_url"]).status_code == 403
    assert api2.patch(f"{url}/{row['id']}", json={"text": "x", "confirmed": True}, headers={"If-Match": "1"}).status_code == 403


def test_upload_limit_and_validation(api):
    user(api, "ocr-limit"); insp = create(api)
    url = f"{URL}/{insp['id']}/text-evidence"
    assert api.post(url, files={"file": ("bad.png", b"bad", "image/png")}).status_code == 422
    # Valid source above old 7 MB middleware limit; whitespace padding is legal in a PDF.
    padded = selectable_pdf() + b"\n" * (8 * 1024 * 1024)
    response = api.post(url, files={"file": ("large.pdf", padded, "application/pdf")})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert api.post(url, files={"file": ("too-big.pdf", b"x" * (ocr.MAX_BYTES + 1), "application/pdf")}).status_code == 413


def test_ocr_migration_preserves_data_and_refuses_destructive_downgrade(tmp_path):
    from test_migrations_phase10 import run, seed_legacy
    from test_migrations_phase11 import seed_phase10
    dbfile = tmp_path / "v.db"
    assert run(tmp_path, "upgrade", "c4e1a9d2b7f3").returncode == 0
    seed_legacy(dbfile); seed_phase10(dbfile)
    with sqlite3.connect(dbfile) as db:
        before = db.execute("select * from inspections").fetchall()
    result = run(tmp_path, "migrate")
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "backups").glob("*.db"))
    with sqlite3.connect(dbfile) as db:
        assert db.execute("select * from inspections").fetchall() == before
        db.execute("""insert into text_evidence
            (id,inspection_id,workspace_id,filename,sha256,object_key,mime,status,engine,pages,original_text,revision,created_by,created_at)
            values ('t1','i1','w1','test.pdf',?,'test/test.pdf','application/pdf','completed','test','[]','test',1,'u1',CURRENT_TIMESTAMP)""", ("a"*64,))
    refused = run(tmp_path, "downgrade", "c4e1a9d2b7f3")
    assert refused.returncode != 0 and "Refusing to discard" in refused.stderr
    with sqlite3.connect(dbfile) as db:
        assert db.execute("select count(*) from text_evidence").fetchone()[0] == 1
    # Empty-schema recovery is tested on a separate throw-away DB.
    other = tmp_path / "empty"; other.mkdir()
    assert run(other, "upgrade").returncode == 0
    assert run(other, "downgrade", "c4e1a9d2b7f3").returncode == 0
    assert run(other, "upgrade").returncode == 0


def test_evidence_rechecks_signoff_after_extraction(api, monkeypatch):
    from visentra_api.models import Inspection, now
    user(api, "ocr-race"); insp = create(api)
    def concurrent_signoff(data, mime):
        with SessionLocal() as db:
            i = db.get(Inspection, insp["id"])
            i.status = "reviewed"
            i.signed_off_at, i.signed_off_by = now(), i.created_by
            i.signoff_outcome = "no_confirmed_findings"
            i.revision += 1
            db.commit()
        return {"status": "no_text", "pages": [], "original_text": "", "engine": "test-only race fixture"}
    monkeypatch.setattr(ocr, "extract", concurrent_signoff)
    url = f"{URL}/{insp['id']}/text-evidence"
    assert api.post(url).status_code == 409
    assert api.get(url).json()["items"] == []


def test_evidence_validation_auth_csrf_and_storage_failure(api, monkeypatch):
    from visentra_api.inspection import text_evidence as T
    assert api.get(f"{URL}/unknown/text-evidence").status_code == 401
    user(api, "ocr-guards"); insp = create(api)
    url = f"{URL}/{insp['id']}/text-evidence"
    assert api.c.post(url).status_code == 403  # no CSRF token
    row = api.post(url, files={"file": ("doc.pdf", selectable_pdf(), "application/pdf")}).json()
    assert api.patch(f"{url}/{row['id']}", json={"text": "x", "confirmed": True}).status_code == 428
    assert api.patch(f"{url}/{row['id']}", json={"text": "x", "confirmed": False}, headers={"If-Match": "1"}).status_code == 422
    def storage_failure(*a, **kw):
        raise OSError("test-only outage")
    monkeypatch.setattr(T.storage, "put", storage_failure)
    response = api.post(url, files={"file": ("different.png", image_bytes(Image.new("RGB", (200, 100), "white")), "image/png")})
    assert response.status_code == 503
    assert len(api.get(url).json()["items"]) == 1


def test_ocr_schema_matches_migration():
    from sqlalchemy import inspect
    from visentra_api.db import engine
    inspector = inspect(engine)
    assert set(TextEvidence.__table__.columns.keys()) == {c["name"] for c in inspector.get_columns("text_evidence")}
    assert {c["name"] for c in inspector.get_unique_constraints("text_evidence")} >= {"uq_text_evidence_source"}
