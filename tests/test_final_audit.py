"""Final completion audit: regression tests for inference paths that had no direct test.

* Detector busy beyond the analysis timeout -> ANALYSIS_BUSY, the inspection is saved, no predictions are invented.
* An exception inside the real Engine.detect (not a monkeypatched detect) -> INFERENCE_FAILED, slot released, retry works.
* Malformed model output -> INVALID_MODEL_OUTPUT, never shown as a completed analysis.
The model is the Phase 13 TEST FIXTURE bundle (random weights): these tests check plumbing, not accuracy.
"""
from __future__ import annotations

from p11_fixtures import create, jpeg, rev, user
from test_phase13 import URL, model_dir  # noqa: F401  (fixture re-export)
from visentra_api.config import settings
from visentra_api.inspection import analysis as AN


def _ready_engine():
    AN.reset()
    eng = AN.engine()[0]
    eng.load()
    return eng


def test_busy_detector_records_analysis_busy_and_saves_the_inspection(api, model_dir, monkeypatch):  # noqa: F811
    user(api, "fa1")
    eng = _ready_engine()
    monkeypatch.setattr(settings, "inspection_analysis_timeout_s", 0.05)
    assert eng._slot.acquire(timeout=1)                    # another analysis holds the only slot
    try:
        b = create(api, jpeg(seed=51))
    finally:
        eng._slot.release()
    assert b["status"] == "analysis_failed" and b["last_error_code"] == "ANALYSIS_BUSY"
    assert b["predictions"] == []
    assert api.get(f"{URL}/{b['id']}").status_code == 200   # saved, retrievable


def test_exception_inside_real_detect_is_inference_failed_and_the_slot_is_released(api, model_dir, monkeypatch):  # noqa: F811
    user(api, "fa2")
    eng = _ready_engine()

    def boom(*a, **k):
        raise RuntimeError("simulated CUDA/CPU failure")
    monkeypatch.setattr(eng.predictor, "raw_detections", boom)
    b = create(api, jpeg(seed=52))
    assert b["status"] == "analysis_failed" and b["last_error_code"] == "INFERENCE_FAILED" and b["predictions"] == []
    assert "simulated" not in (b.get("analysis_note") or "")           # internal exception text is not leaked
    assert eng._slot.acquire(timeout=0.1); eng._slot.release()          # semaphore released after the failure
    monkeypatch.undo()
    monkeypatch.setattr(settings, "detection_models_dir", model_dir)
    monkeypatch.setattr(settings, "inspection_model", "auto")
    AN.reset()
    r = api.post(f"{URL}/{b['id']}/analyses", headers={"If-Match": f'"{rev(api, b["id"])}"'})
    assert r.status_code == 201 and r.json()["status"] == "awaiting_review"


def test_malformed_model_output_is_rejected_not_displayed(api, model_dir, monkeypatch):  # noqa: F811
    user(api, "fa3")
    eng = _ready_engine()
    real = eng.predictor.raw_detections

    def bad(im, timing=None):
        dets, prep, info = real(im, timing) if timing is not None else real(im)
        return [{"cls": 99, "score": 2.0, "xyxy": [5, 5, 1, 1]}], prep, info
    monkeypatch.setattr(eng.predictor, "raw_detections", bad)
    b = create(api, jpeg(seed=53))
    assert b["status"] == "analysis_failed" and b["last_error_code"] == "INVALID_MODEL_OUTPUT" and b["predictions"] == []


def test_confidence_is_never_described_as_a_calibrated_probability_or_certainty():
    """Calibration has not been measured (MODEL_CARD), so neither the report nor the UI may imply it."""
    from pathlib import Path
    from visentra_api.reports import service
    root = Path(__file__).resolve().parents[1]
    texts = " ".join(service.LIMITATIONS) + (root / "apps/web/src/features/aero_pages.js").read_text(encoding="utf-8") \
        + (root / "apps/api/visentra_api/reports/render.py").read_text(encoding="utf-8")
    assert "model's certainty" not in texts
    assert "uncalibrated model score" in " ".join(service.LIMITATIONS)
    assert "not a measured probability" in (root / "apps/web/src/features/aero_pages.js").read_text(encoding="utf-8")


def test_display_tiers_are_validation_derived_and_served_with_the_model(api, model_dir):  # noqa: F811
    """Phase 19: the plain-language tiers come from the validation split and name each band by its measured hit rate."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    t = json.loads((root / "models/detection/VT-DET-0002/display_tiers.json").read_text())
    assert t["model_id"] == "VT-DET-0002" and t["split"] == "valid"
    meta = json.loads((root / "models/detection/VT-DET-0002/metadata.json").read_text())
    for c, v in t["classes"].items():
        assert v["bands"][0]["from"] == meta["thresholds"]["score_threshold"][c]          # starts at the operating threshold
        for b in v["bands"]:
            h = b["hit_rate"]
            assert b["tier"] == ("likely" if h >= 0.5 else "possible" if h >= 0.1 else "unlikely")
    # served only for the matching model (the fixture bundle VT-DET-9001 has none)
    user(api, "fa5")
    d = api.get("/api/v1/detection-model").json()
    assert d.get("display_tiers") is None


def test_guidance_explains_types_inspection_methods_and_every_other_type(api):
    user(api, "fa6")
    g = api.get("/api/v1/defect-guidance").json()
    for c in ("dent", "crack", "corrosion"):
        assert len(g["classes"][c]["types"]) >= 4 and len(g["classes"][c]["inspection_methods"]) >= 3 and g["classes"][c]["severity_factors"]
    assert {"staining", "scratch", "coating", "fastener", "repair", "no_damage", "dirt", "unspecified"} <= set(g["other_types"])
    assert all(o["what_it_is"] and o["next"] for o in g["other_types"].values())
