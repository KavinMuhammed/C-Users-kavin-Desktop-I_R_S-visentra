"""Phase 13: operational, review-assisted detection on every new inspection.

The model used here is a TEST FIXTURE bundle: a randomly initialised YOLOX-nano whose class biases are raised so it
always proposes boxes. It exists only in a temporary directory and only exercises the pipeline (load → verify →
predict → persist → review → report); its outputs mean nothing.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import torch
from PIL import Image

from p11_fixtures import create, decide, jpeg, rev, signoff, user
from visentra_api.config import settings
from visentra_api.db import SessionLocal
from visentra_api.inspection import analysis as AN
from visentra_api.models import AnalysisMedia, AnalysisRun, DefectPrediction, ModelVersion

URL = "/api/v1/inspections"
CLASSES = ["crack", "dent", "corrosion"]
REPO = Path(__file__).resolve().parents[1]


def _bundle(root: Path, model_id="VT-DET-9001", readiness="EVALUATED_LIMITED", supported=CLASSES, bias=4.0) -> Path:
    from visentra_ml.detection import artifact as A
    from visentra_ml.detection import model as Mo
    torch.manual_seed(0)
    m = Mo.build("nano", len(CLASSES))
    sd = m.state_dict()
    for k in sd:
        if ("cls_preds" in k or "obj_preds" in k) and k.endswith("bias"):
            sd[k] = torch.full_like(sd[k], bias)
    base = json.loads((REPO / "models/detection/VT-DET-0001/metadata.json").read_text())
    meta = {k: base[k] for k in A.REQUIRED_META if k in base}
    meta.update(model_id=model_id, version="0.0.1-fixture", variant="nano", classes=CLASSES, supported_classes=list(supported),
                experimental_classes=[c for c in CLASSES if c not in supported], readiness=readiness, input_size=[160, 160],
                thresholds={"score_threshold": {c: 0.3 for c in CLASSES}, "review_threshold": {c: 0.6 for c in CLASSES}, "nms_iou": 0.5},
                known_limitations=["TEST FIXTURE: random weights."])
    return A.package(model_id, sd, meta, out_root=root)


@pytest.fixture()
def model_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="vt-p13-models-"))
    _bundle(d)
    monkeypatch.setattr(settings, "detection_models_dir", d)
    monkeypatch.setattr(settings, "inspection_model", "auto")
    AN.reset()
    yield d
    AN.reset()
    shutil.rmtree(d, ignore_errors=True)


def test_new_inspection_is_analysed_and_predictions_are_stored(api, model_dir):
    user(api, "p13a")
    b = create(api, jpeg(900, 600, seed=3))
    assert b["analysis_mode"] == "model" and b["status"] == "awaiting_review", b.get("analysis_note")
    assert b["analysis"]["status"] == "succeeded" and b["analysis"]["attempt"] == 1
    assert b["predictions"], "the fixture model always proposes boxes"
    p = b["predictions"][0]
    assert p["class_id"] in CLASSES and 0.3 <= p["score"] <= 1 and p["kind"] == "ai_prediction"
    assert all(v["decision"] == "pending" for k, v in b["current_decisions"].items() if k.startswith("prediction:")) or not b["current_decisions"]
    assert b["counts"]["predictions"] == len(b["predictions"]) and b["counts"]["pending"] == len(b["predictions"])
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter_by(inspection_id=b["id"]).one()
        am = db.query(AnalysisMedia).filter_by(analysis_run_id=run.id).one()
        assert (am.oriented_width, am.oriented_height, am.input_width) == (900, 600, 160) and am.n_predictions == len(b["predictions"])
        assert run.model_checksum and len(run.model_checksum) == 64 and run.timings["model_id"] == "VT-DET-9001"
        assert db.get(ModelVersion, run.model_version_id).checksum == run.model_checksum
        for dp in db.query(DefectPrediction).filter_by(inspection_id=b["id"]):
            assert dp.box_x + dp.box_w <= 1.000001 and dp.box_y + dp.box_h <= 1.000001 and dp.box_w > 0


def test_predictions_are_reviewed_and_reported_like_any_proposal(api, model_dir):
    user(api, "p13b")
    b = create(api, jpeg(800, 600, seed=5))
    for p in b["predictions"]:
        r = decide(api, b["id"], "prediction", p["id"], "rejected")
        assert r.status_code in (200, 201), r.text
    r = signoff(api, b["id"])
    assert r.status_code in (200, 201), r.text
    after = api.get(f"{URL}/{b['id']}").json()
    assert after["status"] == "reviewed" and after["counts"]["rejected"] == len(b["predictions"])


def test_model_registry_row_is_reused_across_inspections(api, model_dir):
    user(api, "p13c")
    a, b = create(api, jpeg(seed=11)), create(api, jpeg(seed=12))
    assert a["analysis"]["model"]["version_id"] == b["analysis"]["model"]["version_id"]


def test_analyse_false_and_no_model_keep_the_manual_path(api, model_dir, monkeypatch):
    user(api, "p13d")
    b = create(api, jpeg(seed=21), analyse="false")
    assert (b["analysis_mode"], b["status"], b["predictions"]) == ("none", "awaiting_review", [])
    monkeypatch.setattr(settings, "inspection_model", "off")
    AN.reset()
    c = create(api, jpeg(seed=22))
    assert c["analysis_mode"] == "none" and "switched off" in c["analysis_note"]
    assert api.get("/api/system/capabilities").json()["inspection"]["detection_model"]["available"] is False


def test_ineligible_bundles_are_never_used(api, monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="vt-p13-inel-"))
    try:
        _bundle(d, "VT-DET-9002", readiness="EXPERIMENTAL", supported=[])
        monkeypatch.setattr(settings, "detection_models_dir", d)
        monkeypatch.setattr(settings, "inspection_model", "auto")
        AN.reset()
        assert AN.engine()[0] is None
        monkeypatch.setattr(settings, "inspection_model", "VT-DET-9002")
        AN.reset()
        eng, why = AN.engine()
        assert eng is None and "not eligible" in why
    finally:
        AN.reset()
        shutil.rmtree(d, ignore_errors=True)


def test_a_tampered_bundle_fails_safely_and_the_inspection_is_still_saved(api, monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="vt-p13-bad-"))
    try:
        b = _bundle(d, "VT-DET-9003")
        with open(b / "model.pt", "ab") as f:
            f.write(b"x")
        monkeypatch.setattr(settings, "detection_models_dir", d)
        monkeypatch.setattr(settings, "inspection_model", "auto")
        AN.reset()
        user(api, "p13e")
        r = create(api, jpeg(seed=31))
        assert r["status"] == "analysis_failed" and r["last_error_code"] == "MODEL_UNAVAILABLE" and r["predictions"] == []
        assert "Detection failed" in r["analysis_note"]
        assert api.get(f"{URL}/{r['id']}").status_code == 200
    finally:
        AN.reset()
        shutil.rmtree(d, ignore_errors=True)


def test_failed_analysis_can_be_run_again(api, model_dir, monkeypatch):
    user(api, "p13f")
    monkeypatch.setattr(AN.Engine, "detect", lambda self, data, ref: (_ for _ in ()).throw(AN.AnalysisFailed("INFERENCE_FAILED", "boom")))
    AN.reset()
    eng = AN.engine()[0]
    eng.load()                                            # weights load fine; only inference is broken
    b = create(api, jpeg(seed=41))
    assert b["status"] == "analysis_failed" and b["last_error_code"] == "INFERENCE_FAILED"
    runs = api.get(f"{URL}/{b['id']}/analyses").json()
    assert [r["status"] for r in runs["items"]] == ["failed"] and runs["items"][0]["error_code"] == "INFERENCE_FAILED"
    monkeypatch.undo()
    monkeypatch.setattr(settings, "detection_models_dir", model_dir)
    monkeypatch.setattr(settings, "inspection_model", "auto")
    AN.reset()
    r = api.post(f"{URL}/{b['id']}/analyses", headers={"If-Match": f'"{rev(api, b["id"])}"'})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "awaiting_review" and out["analysis"]["attempt"] == 2 and out["predictions"] and out["last_error_code"] is None
    assert [x["status"] for x in api.get(f"{URL}/{b['id']}/analyses").json()["items"]] == ["succeeded", "failed"]


def test_rerun_is_refused_once_review_has_started(api, model_dir):
    user(api, "p13g")
    b = create(api, jpeg(seed=51))
    decide(api, b["id"], "prediction", b["predictions"][0]["id"], "confirmed")
    r = api.post(f"{URL}/{b['id']}/analyses")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


def test_rerun_needs_update_permission_and_visibility(api, model_dir):
    user(api, "p13h")
    b = create(api, jpeg(seed=61))
    user(api, "p13i")
    assert api.post(f"{URL}/{b['id']}/analyses").status_code == 404
    assert api.get(f"{URL}/{b['id']}/analyses").status_code == 404


def test_detection_model_endpoint_and_capabilities_describe_the_model(api, model_dir):
    user(api, "p13j")
    m = api.get("/api/v1/detection-model").json()
    assert m["model_id"] == "VT-DET-9001" and m["classes"] == CLASSES and m["readiness"] == "EVALUATED_LIMITED"
    assert "engineer" in m["policy"] and "airworthiness" in m["policy"]
    caps = api.get("/api/system/capabilities").json()["inspection"]["detection_model"]
    assert caps["available"] is True and caps["review_required"] is True and caps["model_id"] == "VT-DET-9001"


def test_exif_rotated_upload_boxes_stay_inside_the_oriented_image(api, model_dir):
    user(api, "p13k")
    im = Image.new("RGB", (640, 360), (90, 100, 110))
    exif = im.getexif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    b = create(api, buf.getvalue())
    with SessionLocal() as db:
        am = db.query(AnalysisMedia).join(AnalysisRun, AnalysisRun.id == AnalysisMedia.analysis_run_id).filter(AnalysisRun.inspection_id == b["id"]).one()
        assert (am.oriented_width, am.oriented_height, am.exif_orientation) == (360, 640, 6)


# ----------------------------------------------------------------------------------------- system endpoints (Phase 13)
def test_health_version_and_capabilities_describe_the_inspection_product(api):
    live = api.get("/api/health/live")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    ready = api.get("/api/health/ready").json()
    assert ready["checks"]["database"]["status"] == "ok" and ready["checks"]["object_storage"]["status"] == "ok"
    assert ready["checks"]["ml_runtime"]["status"] == "ok" and "detection_model" in ready["checks"] and "worker" not in ready["checks"]
    v = api.get("/api/v1/system/version").json()
    assert v["name"] == "VisionTra" and "/api/v1" in v["api_prefixes"]
    user(api, "p13sys")
    cap = api.get("/api/v1/system/capabilities").json()
    pol = cap["inspection"]["policy"]
    assert pol["max_upload_mb"] == 5 and pol["min_side"] == 64
    assert {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff", "image/gif", "image/avif"} <= set(pol["formats"])
    from visentra_api.inspection import media as M        # HEIC is advertised only when it can be decoded (Phase 15)
    assert ("image/heic" in pol["formats"]) == (M.register_heif_opener is not None) == pol["heic_supported"]
    assert "training" not in cap and "devices" not in cap and cap["inspection"]["demo_samples"] == {"allowed": False}
    assert api.get("/api/v1/system/hardware").json()["cpu_count"] >= 1


def test_workspace_members_and_isolation(api, api2):
    me = user(api, "p13ws")
    ws = api.post("/api/workspaces", json={"name": "Line maintenance"}).json()
    assert ws["role"] == "OWNER" and ws["inspections"] == 0
    other = f"p13ws-b-{uuid.uuid4().hex[:6]}@example.org"
    api2.register(other)
    assert api2.get(f"/api/workspaces/{ws['id']}").status_code == 404
    r = api.post(f"/api/workspaces/{ws['id']}/members", json={"email": other, "role": "REVIEWER"})
    assert r.status_code == 201 and r.json()["role"] == "REVIEWER"
    b = create(api, jpeg(seed=77), workspace_id=ws["id"])
    assert api2.get(f"{URL}/{b['id']}").status_code == 200                       # a member sees it
    assert api2.patch(f"{URL}/{b['id']}", json={"zone": "x"}, headers={"If-Match": '"0"'}).status_code == 403   # a reviewer cannot edit
    ev = api.get("/api/audit-log?action=inspection.").json()["items"]
    assert any(e["resource_id"] == b["id"] for e in ev)
    assert api2.get("/api/audit-log?action=inspection.").json()["total"] >= 1   # members see the workspace's inspection events


# ----------------------------------------------------------------------------------------- aircraft-surface domain gate
GATE = REPO / "models" / "gate" / "VT-GATE-0001"
needs_gate = pytest.mark.skipif(not (GATE / "coco.pth").is_file(), reason="domain gate bundle not installed (coco.pth is not in git)")
NEG = Path("/home/claude/extdata/imagenet-sample-images")


def _photo_of_a_cat() -> bytes:
    p = REPO / "datasets/negatives/VT-NEG-001/images/n02123045_tabby.JPEG"
    if not p.is_file():
        pytest.skip("negative images not prepared")
    return p.read_bytes()


def _aircraft_surface() -> bytes:
    import csv
    rows = [r for r in csv.DictReader(open(REPO / "datasets/splits/VT-PREP-001/valid.csv", encoding="utf-8")) if r["dataset_id"] == "VT-DS-001"]
    p = REPO / rows[0]["rel_path"]
    if not p.is_file():
        pytest.skip("dataset images not present")
    return p.read_bytes()


@pytest.fixture()
def gated(model_dir, monkeypatch):
    monkeypatch.setattr(settings, "domain_gate", "auto")
    monkeypatch.setattr(settings, "domain_gate_dir", REPO / "models" / "gate")
    AN.reset()
    yield
    AN.reset()


@needs_gate
def test_a_non_aircraft_image_is_saved_but_not_analysed_for_defects(api, gated):
    user(api, "gate-a")
    b = create(api, _photo_of_a_cat())
    assert b["analysis_mode"] == "model" and b["status"] == "awaiting_review" and b["predictions"] == []
    # Phase 15: a gate rejection is not a completed analysis
    assert b["analysis"] is None and b["detection"]["state"] == "relevance_rejected" and b["detection"]["completed"] is False
    assert b["detection"]["gate"]["in_domain"] is False and b["signoff_requires"] == "manual_only"
    assert "does not look like an aircraft surface" in b["analysis_note"]
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter_by(inspection_id=b["id"]).one()
        assert run.status == "cancelled" and run.error_code == "OUT_OF_DOMAIN" and run.suppressed["out_of_domain"] is True
        assert db.query(DefectPrediction).filter_by(inspection_id=b["id"]).count() == 0
    # an engineer can override (reason required, audited); the fixture model then proposes boxes
    h = {"If-Match": f'"{rev(api, b["id"])}"'}
    assert api.post(f"{URL}/{b['id']}/analyses", json={"force": True}, headers=h).status_code == 422
    r = api.post(f"{URL}/{b['id']}/analyses", json={"force": True, "reason": "Close-up of a radome"}, headers=h)
    assert r.status_code == 201 and r.json()["predictions"] and r.json()["analysis"]["domain_gate"]["overridden"] is True
    assert r.json()["detection"]["state"] == "completed_candidates" and r.json()["detection"]["overridden"] is True
    ev = api.get("/api/audit-log?action=inspection.analyse").json()["items"]
    assert ev[0]["meta"]["forced"] is True and ev[0]["meta"]["override_reason"] == "Close-up of a radome"


@needs_gate
def test_an_aircraft_surface_passes_the_gate(api, gated):
    user(api, "gate-b")
    b = create(api, _aircraft_surface())
    g = b["analysis"]["domain_gate"]
    assert g["in_domain"] is True and g["p_aircraft_surface"] >= g["threshold"] and b["analysis"]["out_of_domain"] is False
    assert b["detection"]["state"] in ("completed_candidates", "completed_zero")
    assert api.get("/api/health/ready").json()["checks"]["detection_model"]["domain_gate"] in ("ready", "not_loaded")


def test_a_broken_gate_never_blocks_inspections(api, model_dir, monkeypatch, tmp_path):
    d = tmp_path / "VT-GATE-0099"
    d.mkdir()
    (d / "coco.pth").write_bytes(b"not weights"); (d / "gate.json").write_text("{}"); (d / "SHA256SUMS").write_text("0" * 64 + "  coco.pth\n")
    monkeypatch.setattr(settings, "domain_gate", "auto"); monkeypatch.setattr(settings, "domain_gate_dir", tmp_path)
    AN.reset()
    user(api, "gate-c")
    b = create(api, jpeg(seed=91))
    assert b["status"] == "awaiting_review" and b["predictions"] and b["analysis"]["domain_gate"]["state"] == "failed"
    AN.reset()
