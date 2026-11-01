"""Phase 11: server reports (EP-17…EP-19) — generated from persisted records, pinned to a revision, stored with a checksum,
retrieved securely, versioned, frozen, and failing honestly.

PDF text is checked with `pdftotext` (poppler) and the structure with `qpdf --check` when those tools are installed.
AI predictions used here are TEST FIXTURE rows (tests/p11_fixtures.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from conftest import Api
from p11_fixtures import URL, add_fixture_predictions, create, decide, finding, jpeg, post, rev, signoff, user
from visentra_api.db import SessionLocal, engine
from visentra_api.main import app
from visentra_api.models import AuditEvent, ExperimentalRun, InspectionMedia, InspectionReport, StorageObject, now, uid
from visentra_api.reports import render as R
from visentra_api.reports import service as RS
from visentra_api.security import limiter
from visentra_api.storage import storage

REPO = Path(__file__).resolve().parents[1]
PDFTOTEXT, QPDF = shutil.which("pdftotext"), shutil.which("qpdf")


def err(r):
    return r.json()["error"]


from pdf_assertions import pdf_text


def pdf_pages(data: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", data))


def check_pdf(data: bytes) -> None:
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    if QPDF:
        p = subprocess.run([QPDF, "--check", "-"], input=data, capture_output=True, timeout=60)
        if p.returncode != 0:                                          # older qpdf cannot read stdin: use a file
            f = Path(os.environ.get("VISENTRA_DATA_DIR", "/tmp")) / f"chk-{uuid.uuid4().hex}.pdf"
            f.write_bytes(data)
            p = subprocess.run([QPDF, "--check", str(f)], capture_output=True, timeout=60)
        assert p.returncode == 0, p.stdout.decode() + p.stderr.decode()


@pytest.fixture()
def rp():
    limiter.reset()
    with TestClient(app) as c:
        a = Api(c)
        a.me = user(a, "rpt")
        yield a


def other(prefix="rpo"):
    c = TestClient(app)
    c.__enter__()
    a = Api(c)
    a.me = user(a, prefix)
    return a


def reviewed(api: Api, **fields) -> dict:
    insp = create(api, jpeg(1200, 800, seed=uuid.uuid4().int % 500), **fields)
    finding(api, insp, "crack", comment="Hairline crack, about 40 mm, aft of fastener row 3")
    f2 = finding(api, insp, "corrosion", box={"x": 0.6, "y": 0.5, "w": 0.2, "h": 0.2}).json()["finding"]["id"]
    decide(api, insp["id"], "human_finding", f2, "further_review", comment="Needs NDT eddy-current check")
    r = signoff(api, insp["id"], note="Inspected under raking light")
    assert r.status_code == 200, r.text
    return api.get(f"{URL}/{insp['id']}").json()


def gen(api: Api, iid: str):
    return api.post(f"{URL}/{iid}/reports")


def download(api: Api, rep: dict):
    return api.get(rep["download_url"])


# ================================================================== happy path and content
def test_report_from_a_signed_off_inspection_matches_the_persisted_records(rp):
    insp = reviewed(rp, zone="STA 540 LH", aircraft_ref="Training airframe 2")
    r = gen(rp, insp["id"])
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "available" and rep["version"] == 1 and rep["inspection_revision"] == insp["revision"] and rep["stale"] is False
    assert rep["template_version"] == RS.TEMPLATE and rep["analysis_run_id"] is None and rep["model_version_id"] is None
    assert rep["media_sha256"] == [{"media_id": insp["media"][0]["id"], "sha256": insp["media"][0]["sha256"]}]
    d = download(rp, rep)
    assert d.status_code == 200 and d.headers["content-type"] == "application/pdf" and d.headers["Content-Disposition"].endswith(f'_R1.pdf"')
    assert hashlib.sha256(d.content).hexdigest() == rep["sha256"] and len(d.content) == rep["size_bytes"] and d.headers["Digest"].startswith("sha-256=")
    assert d.headers["X-Content-Type-Options"] == "nosniff" and d.headers["Cache-Control"].startswith("private")
    check_pdf(d.content)
    assert pdf_pages(d.content) == rep["pages"] >= 1
    t = pdf_text(d.content)
    for s in (insp["ref"], rep["id"], "Report R1", f"inspection revision {insp['revision']}", insp["media"][0]["sha256"], "STA 540 LH", "Training airframe 2",
              "Hairline crack, about 40 mm, aft of fastener row 3", "Needs NDT eddy-current check", "Needs further investigation", "Inspected under raking light",
              "Crack", "Corrosion", "Reviewer", "Phase Ten" if False else rp.me["user"]["name"], "No operational defect-detection model analysed this inspection",
              "not an airworthiness certificate", "Outstanding:", "1 of"):
        assert s in t, s
    assert "Not recorded" in t                                                     # camera / taken-at are honestly missing
    assert not re.search(r"defect-free|airworthy|certified|maintenance release(?! ,)", t.replace("not an airworthiness certificate, maintenance release", "")), "unsafe wording"
    with SessionLocal() as db:
        so = db.get(StorageObject, db.get(InspectionReport, rep["id"]).object_key)
        assert so.state == "committed" and so.purpose == "report" and so.sha256 == rep["sha256"] and so.owner_id == rep["id"]
        assert db.query(AuditEvent).filter(AuditEvent.action == "report.generated", AuditEvent.resource_id == rep["id"]).count() == 1
    g = rp.get(f"{URL}/{insp['id']}").json()
    assert g["has_report"] is True and g["report_stale"] is False and g["reports"][0]["id"] == rep["id"]
    assert rp.get(f"/api/v1/reports/{rep['id']}").json()["sha256"] == rep["sha256"]
    assert str(Path(os.environ["VISENTRA_DATA_DIR"])) not in json.dumps(rep) and "object_key" not in json.dumps(rep)


def test_report_shows_predictions_with_their_model_and_the_reviewers_decision(rp):
    insp = create(rp, jpeg(1000, 700, seed=7))
    p1, p2 = add_fixture_predictions(insp["id"], [("dent", 0.83, (0.1, 0.1, 0.2, 0.2)), ("dent", 0.31, (0.5, 0.4, 0.1, 0.1))])
    decide(rp, insp["id"], "prediction", p1, "confirmed", comment="Visible dent, confirmed by touch")
    decide(rp, insp["id"], "prediction", p2, "rejected", comment="Reflection of the hangar light")
    signoff(rp, insp["id"])
    rep = gen(rp, insp["id"]).json()
    assert rep["analysis_run_id"] and rep["model_version_id"]
    t = pdf_text(download(rp, rep).content)
    for s in ("TEST-FIXTURE-DET v3", p1, p2, "0.83", "0.31 (low)", "Confirmed defect", "Rejected", "AI prediction", "Visible dent, confirmed by touch",
              "Reflection of the hangar light", "not a defect grade"):
        assert s in t, s


def test_zero_findings_and_many_findings(rp):
    empty = create(rp)
    signoff(rp, empty["id"])
    t = pdf_text(download(rp, gen(rp, empty["id"]).json()).content)
    assert "No finding was recorded for this inspection revision" in t and "does not establish that the inspected surface" in t
    big = create(rp, jpeg(1600, 1000, seed=3))
    for k in range(60):
        r = finding(rp, big, ["crack", "dent", "corrosion", "other"][k % 4], box={"x": (k % 10) * 0.09, "y": (k // 10) * 0.15, "w": 0.08, "h": 0.1},
                    comment=f"finding {k + 1} " + "long-note " * 12)
        assert r.status_code == 201
    signoff(rp, big["id"])
    rep = gen(rp, big["id"]).json()
    data = download(rp, rep).content
    check_pdf(data)
    assert rep["pages"] >= 4
    t = pdf_text(data)
    for k in (1, 30, 60):
        assert f"finding {k} " in t
    assert f"Page {rep['pages']} of {rep['pages']}" in t


def test_long_and_non_latin_comments_wrap_and_never_break_the_pdf(rp):
    insp = create(rp)
    long = ("Detailed observation: " + "x" * 120 + " " + "the quick brown fox jumps over the lazy dog " * 8)[:600]
    finding(rp, insp, "dent", comment=long)
    finding(rp, insp, "other", comment="Ünïcödé ok — 中文 and emoji 🚀 (shown as ?) ) ( \\ BT ET Tj")
    signoff(rp, insp["id"])
    data = download(rp, gen(rp, insp["id"]).json()).content
    check_pdf(data)
    t = pdf_text(data)
    assert "Detailed observation:" in t and "lazy dog" in t and "Ünïcödé ok" in t and "BT ET Tj" in t


def test_experimental_runs_are_excluded_but_disclosed(rp):
    insp = create(rp)
    with SessionLocal() as db:
        db.add(ExperimentalRun(id=uid(), user_id=rp.me["user"]["id"], workspace_id=insp["workspace"]["id"], inspection_id=insp["id"], media_id=insp["media"][0]["id"],
                               source="inspection_media", model_id="VT-DET-0001", model_version="0.1.0", readiness="EVALUATED_LIMITED", status="completed",
                               outcome="detections", prediction_count=2, settings={}, timing_ms={}))
        db.commit()
    signoff(rp, insp["id"])
    t = pdf_text(download(rp, gen(rp, insp["id"]).json()).content)
    assert "1 experimental model run(s)" in t and "excluded from reports" in t and "dent (experimental)" not in t


# ================================================================== lifecycle, idempotency, versioning
def test_report_needs_a_signed_off_inspection(rp):
    insp = create(rp)
    r = gen(rp, insp["id"])
    assert r.status_code == 409 and err(r)["code"] == "REPORT_NOT_READY" and err(r)["details"]["reason"] == "not_signed_off"
    finding(rp, insp)
    assert gen(rp, insp["id"]).status_code == 409
    with SessionLocal() as db:
        assert db.query(InspectionReport).filter_by(inspection_id=insp["id"]).count() == 0


def test_repeated_and_concurrent_requests_produce_one_report(rp):
    insp = reviewed(rp)
    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: gen(rp, insp["id"]), range(4)))
    ok = [r for r in rs if r.status_code in (200, 201)]
    assert ok and all(r.status_code in (200, 201, 409) for r in rs), [r.text for r in rs]
    assert len({r.json()["id"] for r in ok}) == 1
    again = gen(rp, insp["id"])
    assert again.status_code == 200 and again.headers.get("X-Report-Existing") == "true" and again.json()["id"] == ok[0].json()["id"]
    with SessionLocal() as db:
        rows = db.query(InspectionReport).filter_by(inspection_id=insp["id"]).all()
        assert [r.status for r in rows] == ["available"]
        assert db.query(StorageObject).filter(StorageObject.purpose == "report", StorageObject.owner_id == rows[0].id).count() == 1


def test_new_revision_new_version_old_version_kept_stale_and_frozen(rp):
    insp = reviewed(rp)
    r1 = gen(rp, insp["id"]).json()
    pdf1 = download(rp, r1).content
    assert post(rp, insp["id"], "reopen", {"comment": "add a finding"}, rev(rp, insp["id"])).status_code == 200
    finding(rp, insp, "dent", comment="Second-look dent")
    signoff(rp, insp["id"])
    r2 = gen(rp, insp["id"])
    assert r2.status_code == 201
    r2 = r2.json()
    assert r2["version"] == 2 and r2["id"] != r1["id"] and r2["inspection_revision"] > r1["inspection_revision"]
    meta1 = rp.get(f"/api/v1/reports/{r1['id']}").json()
    assert meta1["stale"] is True and meta1["version"] == 1 and meta1["sha256"] == r1["sha256"]
    assert download(rp, meta1).content == pdf1                                        # the old version is unchanged and retrievable
    assert "Second-look dent" in pdf_text(download(rp, r2).content) and "Second-look dent" not in pdf_text(pdf1)
    g = rp.get(f"{URL}/{insp['id']}").json()
    assert [x["version"] for x in g["reports"]] == [2, 1] and g["report_stale"] is False
    for sql in ("UPDATE inspection_reports SET sha256 = :h WHERE id = :i", "DELETE FROM inspection_reports WHERE id = :i"):
        with engine.begin() as c, pytest.raises((IntegrityError, OperationalError)):
            c.execute(text(sql), {"i": r1["id"], "h": "0" * 64})
    with engine.begin() as c:                                                         # retention may still record a purge time
        c.execute(text("SELECT 1"))


def test_report_pins_the_revision_it_was_built_from(rp):
    """A report built at revision n shows the decisions of revision n, even when generated after nothing else changed."""
    insp = reviewed(rp)
    rep = gen(rp, insp["id"]).json()
    with SessionLocal() as db:
        snap_rev = db.get(InspectionReport, rep["id"]).inspection_revision
    assert snap_rev == insp["revision"]
    t = pdf_text(download(rp, rep).content)
    revs = [int(x) for x in re.findall(r"(?<![\d:-])(\d+) \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", t)]
    assert revs and max(revs) == snap_rev


# ================================================================== failures
def test_render_failure_is_recorded_and_nothing_is_stored(rp, monkeypatch):
    insp = reviewed(rp)
    monkeypatch.setattr(R, "render", lambda snap: (_ for _ in ()).throw(RuntimeError("font table broken")))
    with SessionLocal() as db:
        before = db.query(StorageObject).filter(StorageObject.purpose == "report").count()
    r = gen(rp, insp["id"])
    assert r.status_code == 500 and err(r)["code"] == "REPORT_GENERATION_FAILED" and "Nothing was stored" in err(r)["message"]
    rid = err(r)["details"]["report_id"]
    with SessionLocal() as db:
        row = db.get(InspectionReport, rid)
        assert row.status == "failed" and row.error_code == "REPORT_GENERATION_FAILED" and row.object_key is None
        assert db.query(StorageObject).filter(StorageObject.purpose == "report", StorageObject.state != "deleted").count() == before
    assert rp.get(f"/api/v1/reports/{rid}/download").status_code == 409
    monkeypatch.undo()
    ok = gen(rp, insp["id"])                                                          # a failed attempt never blocks a retry
    assert ok.status_code == 201 and ok.json()["id"] != rid


def test_storage_failure_removes_partial_output(rp, monkeypatch):
    insp = reviewed(rp)
    real = storage.put

    def put(key, data, ct="application/octet-stream"):
        if key.startswith("inspection-reports/"):
            raise OSError("disk full")
        return real(key, data, ct)
    monkeypatch.setattr(storage, "put", put)
    r = gen(rp, insp["id"])
    assert r.status_code == 500 and err(r)["code"] == "REPORT_GENERATION_FAILED"
    with SessionLocal() as db:
        assert db.query(StorageObject).filter(StorageObject.purpose == "report", StorageObject.state == "pending").count() == 0


def test_tampered_original_blocks_the_report(rp):
    insp = reviewed(rp)
    with SessionLocal() as db:
        key = db.scalar(select(InspectionMedia.object_key).where(InspectionMedia.inspection_id == insp["id"]))
    p = storage._path(key)
    good = p.read_bytes()
    p.write_bytes(good[:-8] + b"tampered")
    try:
        r = gen(rp, insp["id"])
        assert r.status_code == 409 and err(r)["code"] == "INTEGRITY_FAILED"
        with SessionLocal() as db:
            assert db.get(InspectionReport, err(r)["details"]["report_id"]).status == "failed"
    finally:
        p.write_bytes(good)


def test_inspection_change_during_generation_fails_the_report(rp, monkeypatch):
    insp = reviewed(rp)
    real = R.render

    def render_then_change(snap):
        out = real(snap)
        with engine.begin() as c:                                                      # another user edits metadata meanwhile
            c.execute(text("UPDATE inspections SET revision = revision + 1 WHERE id = :i"), {"i": insp["id"]})
        return out
    monkeypatch.setattr(R, "render", render_then_change)
    r = gen(rp, insp["id"])
    assert r.status_code == 409 and err(r)["code"] == "REVIEW_CONFLICT"
    with SessionLocal() as db:
        row = db.get(InspectionReport, err(r)["details"]["report_id"])
        assert row.status == "failed" and row.object_key is None
        assert db.query(StorageObject).filter(StorageObject.purpose == "report", StorageObject.state == "committed", StorageObject.owner_id == row.id).count() == 0


def test_interrupted_generation_is_closed_and_retried(rp):
    insp = reviewed(rp)
    with SessionLocal() as db:
        db.add(InspectionReport(id=uid(), inspection_id=insp["id"], inspection_revision=insp["revision"], template_version=RS.TEMPLATE, status="generating",
                                requested_by=rp.me["user"]["id"], requested_at=now() - RS.STALE_GENERATING * 2, media_sha256=[]))
        db.commit()
    r = gen(rp, insp["id"])
    assert r.status_code == 201
    with SessionLocal() as db:
        st = sorted((x.status, x.error_code or "") for x in db.query(InspectionReport).filter_by(inspection_id=insp["id"]))
        assert st == [("available", ""), ("failed", "WORKER_LOST")]


def test_tampered_stored_report_is_not_served(rp):
    insp = reviewed(rp)
    rep = gen(rp, insp["id"]).json()
    with SessionLocal() as db:
        p = storage._path(db.get(InspectionReport, rep["id"]).object_key)
    good = p.read_bytes()
    p.write_bytes(good + b"%tamper")
    try:
        r = download(rp, rep)
        assert r.status_code == 409 and err(r)["code"] == "INTEGRITY_FAILED"
    finally:
        p.write_bytes(good)                     # leave the shared store consistent for later tests (storage reconcile --verify)


# ================================================================== access control
@pytest.mark.parametrize("role,create_ok,read_ok", [("VIEWER", False, True), ("ML_ENGINEER", True, True), ("REVIEWER", True, True)])
def test_report_permissions_follow_the_workspace_role(rp, role, create_ok, read_ok):
    ws = rp.post("/api/workspaces", json={"name": f"Hangar {uuid.uuid4().hex[:5]}"}).json()["id"]
    insp = reviewed(rp, workspace_id=ws)
    rep = gen(rp, insp["id"]).json()
    o = other()
    try:
        assert rp.post(f"/api/workspaces/{ws}/members", json={"email": o.me["user"]["email"], "role": role}).status_code == 201
        assert (o.get(f"/api/v1/reports/{rep['id']}").status_code == 200) is read_ok
        assert (download(o, rep).status_code == 200) is read_ok
        r = gen(o, insp["id"])
        assert r.status_code == (200 if create_ok else 403)
    finally:
        o.c.__exit__(None, None, None)


def test_a_guessed_report_id_reveals_nothing(rp):
    insp = reviewed(rp)
    rep = gen(rp, insp["id"]).json()
    o = other()
    try:
        for u in (f"/api/v1/reports/{rep['id']}", rep["download_url"], f"/api/v1/reports/{'0' * 32}"):
            r = o.get(u)
            assert r.status_code == 404 and err(r)["code"] == "NOT_FOUND", u
        assert gen(o, insp["id"]).status_code == 404
    finally:
        o.c.__exit__(None, None, None)
    assert TestClient(app).get(rep["download_url"]).status_code == 401


def test_report_survives_restart(rp):
    insp = reviewed(rp)
    rep = gen(rp, insp["id"]).json()
    engine.dispose()
    code = ("from visentra_api.db import SessionLocal\nfrom visentra_api.models import InspectionReport\nfrom visentra_api.storage import storage\nimport hashlib, sys\n"
            "r = SessionLocal().get(InspectionReport, sys.argv[1]); print(r.status, hashlib.sha256(storage.get(r.object_key)).hexdigest() == r.sha256)\n")
    env = {**os.environ, "PYTHONPATH": f"{REPO / 'apps/api'}{os.pathsep}{REPO / 'ml'}"}
    p = subprocess.run([sys.executable, "-c", code, rep["id"]], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0 and p.stdout.strip().splitlines()[-1] == "available True", p.stderr
