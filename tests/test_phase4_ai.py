"""Phase 4: aircraft relevance, diagnostics, class mapping, hard cases and evidence-based guidance.

The hard-case regressions run the real VT-DET-0002 bundle and VT-GATE-0002 on the stored user uploads (P4-A..P4-K in
docs/evidence/phase4/hard_cases_manifest.json). They pin what the production pipeline does on those images today, so a
change to preprocessing, postprocessing, class mapping or thresholds that alters an outcome is noticed. They are not a
measure of model quality. The originals are local user uploads with unknown reuse rights: the tests skip where absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from p11_fixtures import create, decide, finding, signoff, user
from test_phase13 import model_dir  # noqa: F401 — fixture: a packaged random-weights detector
from test_phase15 import rejecting_gate  # noqa: F401 — fixture: the INC-15-01 gate verdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import output as O  # noqa: E402

MANIFEST = ROOT / "docs/evidence/phase4/hard_cases_manifest.json"
STABLE = ROOT / "models/detection/VT-DET-0002"
GATE = ROOT / "models/gate/VT-GATE-0002"
URL = "/api/v1/inspections"


# ------------------------------------------------------------------ diagnostics are kept, never promoted to detections
def _meta():
    return {"model_id": "VT-DET-T", "version": "1", "readiness": "EVALUATED_LIMITED", "classes": ["crack", "dent", "corrosion"],
            "supported_classes": ["crack", "dent", "corrosion"], "experimental_classes": []}


PREP = {"mode": "letterbox", "S_w": 416, "S_h": 416, "r_x": 0.5, "r_y": 0.5, "pad_x": 0, "pad_y": 0}


def test_below_threshold_candidates_are_kept_as_diagnostics_not_detections():
    dets = [{"cls": 1, "score": s, "xyxy": [10 + i, 10, 60 + i, 50]} for i, s in enumerate([0.02, 0.0107, 0.4, 0.003, 0.002, 0.0015, 0.001])]
    o = O.build(_meta(), {"width": 100, "height": 100}, PREP, dets, score_threshold={"crack": 0.025, "dent": 0.025, "corrosion": 0.275}, nms_iou=0.65)
    assert [d["score"] for d in o["detections"]] == [0.4]
    s = o["suppressed"]
    assert s["below_threshold"] == 6 and s["candidates_after_nms"] == 7
    top = s["below_threshold_top"]
    assert len(top) == O.DIAGNOSTIC_TOP and [t["score"] for t in top] == sorted((t["score"] for t in top), reverse=True)
    assert top[0] == {"class_name": "dent", "score": 0.02, "threshold": 0.025, "box_normalised": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.4}}
    assert all("ordinal" not in t for t in top)


def test_zero_detections_still_carry_the_no_detection_warning_and_diagnostics():
    o = O.build(_meta(), {"width": 100, "height": 100}, PREP, [{"cls": 0, "score": 0.01, "xyxy": [1, 1, 9, 9]}],
                score_threshold={"crack": 0.025, "dent": 0.025, "corrosion": 0.275}, nms_iou=0.65)
    assert o["status"] == "no_detections" and O.NO_DETECTION_WARNING in o["warnings"]
    assert o["suppressed"]["below_threshold_top"][0]["class_name"] == "crack"


# ------------------------------------------------------------------ class mapping
def test_stable_detector_class_order_matches_the_training_mapping():
    meta = json.loads((STABLE / "metadata.json").read_text())
    from visentra_ml.detection import data as Dt
    cfg = json.loads((ROOT / "ml/configs/detection/VT-EXP-010.json").read_text())
    assert meta["classes"] == Dt.load_mapping(cfg)["targets"] == ["crack", "dent", "corrosion"]
    assert set(meta["thresholds"]["score_threshold"]) == set(meta["classes"])


# ------------------------------------------------------------------ guidance follows the human outcome (PDF)
def _report_text(api, iid):
    from pdf_assertions import pdf_text
    rep = api.post(f"{URL}/{iid}/reports").json()
    rep = rep.get("report") or rep
    return pdf_text(api.get(rep["download_url"]).content)


def test_report_gives_no_repair_guidance_for_an_unconfirmed_class(api, rejecting_gate):  # noqa: F811
    user(api, "p4-guide-a")
    b = create(api)
    r = finding(api, b, "crack")
    assert r.status_code == 201, r.text
    fid = (r.json().get("finding") or r.json())["id"]
    assert decide(api, b["id"], "human_finding", fid, "further_review").status_code in (200, 201)
    s = signoff(api, b["id"], ack="manual_only")
    if s.status_code != 200:
        pytest.skip(f"sign-off with an open further-review finding is refused by policy ({s.status_code})")
    flat = _report_text(api, b["id"])
    assert "Not confirmed, needs further investigation" in flat and "Crack" in flat
    assert "Repair options" not in flat and "Stop-drilling" not in flat


def test_report_keeps_guidance_for_a_confirmed_class(api, rejecting_gate):  # noqa: F811
    user(api, "p4-guide-b")
    b = create(api)
    assert finding(api, b, "corrosion").status_code == 201
    assert signoff(api, b["id"], ack="manual_only").status_code == 200
    flat = _report_text(api, b["id"])
    # scientific review 2026-10-29: a confirmed class gets evidence steps and its unknowns, never repair options
    assert "Maintenance guidance" in flat and "Evidence still to collect" in flat and "Not confirmed" not in flat
    assert "Engineering disposition: not established" in flat and "does not authorise a repair" in flat
    assert "Repair options" not in flat and "Stop-drilling" not in flat


# ------------------------------------------------------------------ hard cases through the real models
def _cases():
    if not MANIFEST.is_file():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def runners():
    if not (STABLE / "model.pt").is_file() or not (GATE / "coco.pth").is_file():
        pytest.skip("stable detector or gate bundle not present")
    import trace_hard_cases as T
    from visentra_ml.detection import domain_gate as DG
    return T.Runner(STABLE), DG.Gate(GATE, threads=2)


# (case, gate decision, candidates after NMS, retained at the stable thresholds, retained classes) — measured 2026-10-24
# and identical to the stored production analysis runs for the same sha256.
EXPECTED = {
    "P4-A": ("uncertain", 21, 0, []),
    "P4-B": ("relevant", 5, 0, []),
    "P4-C": ("relevant", 8, 0, []),
    "P4-D": ("irrelevant", 14, 2, ["dent", "dent"]),
    "P4-D2": ("relevant", 16, 2, ["crack", "crack"]),
    "P4-E": ("relevant", 2, 0, []),
    "P4-I": ("relevant", 4, 3, ["dent", "dent", "dent"]),
    "P4-J": ("relevant", 7, 3, ["corrosion", "corrosion", "corrosion"]),
}


@pytest.mark.parametrize("case_id", sorted(EXPECTED))
def test_hard_case_pipeline_outcome_is_reproduced(case_id, runners):
    from PIL import Image
    c = next((c for c in _cases() if c["case_id"] == case_id), None)
    p = ROOT / "data/objects" / c["object_key"] if c else None
    if p is None or not p.is_file():
        pytest.skip(f"{case_id} original not available in this checkout")
    det, gate = runners
    decision, n_nms, n_kept, kept_cls = EXPECTED[case_id]
    with Image.open(p) as im:
        im.load()
        state, _ = gate.decide(gate.evidence(im))
        t = det.trace(im, c.get("expected_core") or [])
    assert state == decision
    assert t["candidates_post_nms"] == n_nms and t["retained_at_thresholds"] == n_kept
    assert [r["class"] for r in t["retained"]] == kept_cls


def test_low_relevance_score_on_a_whole_aircraft_photo_is_not_a_rejection(runners):
    """P4-E: the surface model's p is only 0.146, but the airplane detector finds the aircraft: relevant, not rejected."""
    from PIL import Image
    c = next((c for c in _cases() if c["case_id"] == "P4-E"), None)
    p = ROOT / "data/objects" / c["object_key"] if c else None
    if p is None or not p.is_file():
        pytest.skip("P4-E original not available")
    _, gate = runners
    with Image.open(p) as im:
        im.load(); ev = gate.evidence(im)
    assert ev["p"] < gate.threshold and ev["airplane"] >= gate.airplane_min
    assert gate.decide(ev)[0] == "relevant"


# ------------------------------------------------------------------ VT-DET-0001 integration fixture: platform-independent inputs
def _fx():
    import inference_fixture as F
    return F


def _png(im, **kw):
    import io as _io
    b = _io.BytesIO(); im.save(b, format="PNG", **kw); return b.getvalue()


def test_fixture_accepts_a_synthetic_image_re_encoded_with_identical_pixels():
    F = _fx()
    ref, data = F.synthetic_images()[0]
    from PIL import Image
    import io as _io
    with Image.open(_io.BytesIO(data)) as im:
        im.load(); other = _png(im, compress_level=1)          # same pixels, different PNG bytes (as on another platform)
    assert other != data and F.content_sha256(other) == F.content_sha256(data)
    rec = {"sha256": "recorded-on-linux", "content_sha256": F.content_sha256(data)}
    assert F.same_input(ref, rec, {"sha256": "x", "content_sha256": F.content_sha256(other)})


def test_fixture_still_rejects_changed_pixels_orientation_and_validation_bytes():
    F = _fx()
    from PIL import Image
    a = Image.new("RGB", (64, 48), (150, 152, 158))
    b = a.copy(); b.putpixel((10, 10), (151, 152, 158))         # one pixel, one channel, +1
    ex = Image.Exif(); ex[0x0112] = 6
    rot = _png(a, exif=ex.tobytes())
    ca, cb, cr = F.content_sha256(_png(a)), F.content_sha256(_png(b)), F.content_sha256(rot)
    assert len({ca, cb, cr}) == 3                               # pixel change and EXIF orientation both change identity
    rec = {"sha256": "s", "content_sha256": ca}
    assert not F.same_input("synthetic-x", rec, {"sha256": "t", "content_sha256": cb})
    assert not F.same_input("synthetic-x", rec, {"sha256": "t", "content_sha256": cr})
    # validation files stay byte-strict even when their decoded content would match
    assert not F.same_input("valid-1", rec, {"sha256": "t", "content_sha256": ca})
    # a synthetic entry recorded without content_sha256 falls back to the byte check
    assert not F.same_input("synthetic-x", {"sha256": "s"}, {"sha256": "t", "content_sha256": ca})
    assert F.same_input("valid-1", rec, {"sha256": "s", "content_sha256": "anything"})


def test_recorded_fixture_identifies_only_synthetic_images_by_content():
    spec = json.loads((ROOT / "ml/fixtures/VT-DET-0001.inference_fixture.json").read_text())
    F = _fx()
    gen = dict(F.synthetic_images())
    for ref, r in spec["results"].items():
        if ref.startswith("synthetic-"):
            assert r["content_sha256"] == F.content_sha256(gen[ref])
        else:
            assert "content_sha256" not in r
