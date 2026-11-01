"""Scientific review 2026-10-29: the per-finding proposal engine (inspection/proposal.py) and the corrected class guidance.

Three layers:
  · unit scenarios build proposals from serialised inspection records (the shape GET /inspections/{id} returns), so each case
    states exactly which evidence exists; they test the text and state logic, not model quality;
  · API scenarios use the real review endpoints (correction, history retrieval, PDF report) on TEST FIXTURE predictions;
  · one scenario runs the real VT-DET-0002 bundle on a stored upload and feeds its genuine output to the engine (skips when
    the bundle or the image is not in this checkout).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from p11_fixtures import add_fixture_predictions, create, decide, signoff, user

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/inspections"
STABLE = ROOT / "models/detection/VT-DET-0002"

from visentra_api.inspection import guidance as G  # noqa: E402
from visentra_api.inspection import proposal as PR  # noqa: E402


def rec(preds=(), humans=(), decisions=None, aircraft="VT-ABC", component="fuselage", zone="STA 540 LH", detection=None, model_id="VT-DET-0002"):
    """A serialised inspection with the fields the engine reads."""
    return {"ref": "INS-T-1", "aircraft_ref": aircraft, "component": component, "zone": zone,
            "media": [{"id": "m1", "width": 1600, "height": 1200}],
            "analysis": {"model_id": model_id, "finished_at": "2026-10-29T10:00:00Z", "model": {"version": "v1.0.0"}} if model_id else None,
            "predictions": [{"id": f"p{k}", "media_id": "m1", "class_id": c, "score": s, "low_confidence": s < 0.05, "box": dict(zip("xywh", b)),
                             "clipped": False, "predicted_at": "2026-10-29T10:00:00Z"} for k, (c, s, b) in enumerate(preds, 1)],
            "human_findings": [{"id": f"h{k}", "media_id": "m1", "category": c, "box": dict(zip("xywh", b)), "created_by": {"id": "u1", "name": "R. Reviewer"},
                                "created_at": "2026-10-29T11:00:00Z"} for k, (c, b) in enumerate(humans, 1)],
            "current_decisions": decisions or {}, "detection": detection or {"state": "completed_candidates", "completed": True}}


def text(p: dict) -> str:
    return json.dumps(p, ensure_ascii=False)


DENT = ("dent", 0.31, (0.40, 0.40, 0.10, 0.08))
NUMBER_WITH_UNIT = re.compile(r"\b\d+(\.\d+)?\s?(mm|in|inch|inches|cm|thou)\b", re.I)


# ------------------------------------------------------------------ 1. provisional dent classification
def test_dent_proposal_is_provisional_and_keeps_stages_separate():
    p = PR.build(rec([DENT]))["p1"]
    assert p["assessment"]["status"] == "provisional" and p["assessment"]["predicted_class"] == "dent"
    assert p["stages"]["inspector"]["status"] == "not_reviewed" and p["stages"]["inspector"]["class"] is None
    assert p["identity"]["class_basis"] == "ai_proposal"
    assert p["stages"]["disposition"]["status"] == "not_established"
    obs = " ".join(p["observation"]["statements"])
    assert "does not measure surface geometry" in obs and "640" not in obs
    assert "160 × 96 px" in obs                    # derived from the stored box and image size, nothing else


# ------------------------------------------------------------------ 2. ambiguous: dent vs an overlapping alternative
def test_overlapping_other_class_makes_the_proposal_ambiguous():
    r = PR.build(rec([DENT, ("crack", 0.12, (0.41, 0.40, 0.09, 0.08))]))
    a = r["p1"]["assessment"]
    assert a["status"] == "ambiguous" and a["competing"][0]["class"] == "crack" and a["competing"][0]["score"] == 0.12
    assert "neither a crack nor its absence is established" in text(r["p1"])
    # no fabricated distribution: only the proposals the detector actually returned are listed
    lone = PR.build(rec([DENT]))["p1"]["assessment"]
    assert lone["competing"] == [] and "scores for other classes are not available" in lone["competing_note"]


# ------------------------------------------------------------------ 3. a crease is an observation, not a crack
def test_crease_guidance_does_not_establish_or_exclude_a_crack():
    g = G.for_class("dent")
    crease = next(t for t in g["types"] if "crease" in t)
    assert "does not establish that a crack exists" in crease
    assert any("no visible crack does not mean no crack" in u for u in g["unknowns"])
    assert not re.search(r"crack[- ]free|no crack(s)? (is|are) present|rule[sd]? out crack", text(g), re.I)


# ------------------------------------------------------------------ 4. material loss cannot be established
def test_no_claim_that_material_was_not_lost():
    g = G.for_class("dent")
    assert "no loss of material" not in text(g).lower()
    assert any("material loss" in u and "neither establish nor exclude" in u for u in g["unknowns"])
    assert any("material loss" in u for u in PR.build(rec([DENT]))["p1"]["unknowns"])


# ------------------------------------------------------------------ 5. several depressions, no hail attribution
def test_multiple_dents_are_counted_without_attributing_hail():
    r = PR.build(rec([DENT, ("dent", 0.2, (0.1, 0.1, 0.05, 0.05)), ("dent", 0.1, (0.7, 0.7, 0.05, 0.05))]))
    obs = " ".join(r["p1"]["observation"]["statements"])
    assert "3 findings of class 'dent'" in obs and "do not by themselves establish a cause such as hail" in obs
    assert r["p1"]["mechanism"]["statement"].startswith("Not established")
    g = G.for_class("dent")
    assert not any("hail" in t.lower() for t in g["types"])          # hail is a mechanism, not a morphology type
    assert "least severe" not in text(g).lower() and "typically leaves" not in text(g).lower()


# ------------------------------------------------------------------ 6. structural location unknown
def test_unknown_location_is_not_inferred():
    p = PR.build(rec([DENT], component="other", zone=None))["p1"]
    ctx = {c["field"]: c for c in p["identity"]["context"]}
    assert not ctx["Component"]["established"] and ctx["Component"]["display"] == PR.NOT_ESTABLISHED
    assert ctx["Structural features (frame, stringer, fastener row, edge, joint)"]["display"].startswith(PR.NOT_ESTABLISHED)
    assert any("exact structural location" in u for u in p["unknowns"])
    assert "near a fastener" not in " ".join(p["observation"]["statements"]).lower()


# ------------------------------------------------------------------ 7. aircraft identity and manual missing
def test_missing_aircraft_identity_blocks_manual_identification():
    p = PR.build(rec([DENT], aircraft=None))["p1"]
    req = {r["item"]: r for r in p["stages"]["disposition"]["requirements"]}
    assert not req["Aircraft identity"]["met"] and "applicable manual cannot be identified" in req["Aircraft identity"]["state"]
    assert not req["Applicable aircraft-specific data (SRM, AMM or other approved data) and revision"]["met"]
    assert "Acceptance limits and repair disposition have not been established" in p["stages"]["disposition"]["statement"]


# ------------------------------------------------------------------ 8. uncalibrated score semantics
def test_score_is_described_as_uncalibrated_and_tier_as_in_sample():
    a = PR.build(rec([DENT]))["p1"]["assessment"]
    assert "not a calibrated probability" in a["score_semantics"] and "not size, severity" in a["score_semantics"]
    if a["validation_tier"]:                           # present when the VT-DET-0002 bundle is installed
        assert "in-sample" in a["validation_tier"]["semantics"] and a["validation_tier"]["split"] == "valid"
    assert "severity" not in {k for k in a}            # no severity field derived from the score


def test_low_score_is_uncertain_not_minor():
    a = PR.build(rec([("dent", 0.03, (0.4, 0.4, 0.1, 0.1))]))["p1"]["assessment"]
    assert a["status"] == "low_score" and "minor" not in text(a).lower() and "small" not in a["status_label"].lower()


# ------------------------------------------------------------------ 9. unsupported / unknown class
def test_unknown_class_falls_back_to_other_without_inventing_content():
    g = G.for_class("hail-damage")
    assert g["class"] == "other" and g["types"] == [] and g["severity_factors"] == []
    p = PR.build(rec(humans=[("other", (0.1, 0.1, 0.2, 0.2))]))["h1"]
    assert p["identity"]["class_shown"] == "other" and p["assessment"] is None


# ------------------------------------------------------------------ 10. inadequate input
@pytest.mark.parametrize("det", [{"relevance_uncertain": True}, {"screening_failed": True}, {"overridden": True}])
def test_unestablished_relevance_marks_the_input_inadequate(det):
    p = PR.build(rec([DENT], detection={"state": "completed_candidates", **det}))["p1"]
    assert p["assessment"]["status"] == "input_inadequate"
    assert p["evidence_steps"][0].startswith("First establish that the image shows the aircraft surface")


# ------------------------------------------------------------------ 12. confirmed class, disposition still undetermined
def test_confirmed_class_does_not_establish_a_disposition():
    d = {"prediction:p1": {"decision": "confirmed", "corrected_class_id": None, "actor": {"id": "u1", "name": "R. Reviewer"}, "at": "2026-10-29T12:00:00Z"}}
    p = PR.build(rec([DENT], decisions=d))["p1"]
    assert p["stages"]["inspector"]["status"] == "confirmed" and p["stages"]["inspector"]["by"]["name"] == "R. Reviewer"
    assert p["identity"]["class_basis"] == "inspector" and p["stages"]["ai"]["class"] == "dent"
    assert p["stages"]["disposition"]["status"] == "not_established" and "does not authorise a repair" in p["stages"]["disposition"]["statement"]


# ------------------------------------------------------------------ 13. references are traceable and scoped
def test_references_carry_verified_metadata_and_scope():
    for s in G.SOURCES:
        assert s["url"].startswith("https://") and s["org"] and s["scope"] and s["supports"] and s["verified_on"] == G.VERIFIED_ON
        assert s["role"] in ("background", "regulatory_context")
    ac = G.SOURCE_BY_ID["AC43.13-1B"]
    assert "nonpressurized" in ac["scope"] and "Not a universal" in ac["scope"] and ac["date"] == "1998-09-08"
    assert "AC120-77A" not in G.SOURCE_BY_ID and "cancels AC 120-77A" in G.SOURCE_BY_ID["AC120-77B"]["revision"]
    assert G.references(["no-such-doc"]) == []                     # an unknown reference is dropped, never synthesised


# ------------------------------------------------------------------ safety boundaries across all classes
@pytest.mark.parametrize("cls", ["dent", "crack", "corrosion", "other"])
def test_no_invented_limits_repairs_or_ndt_prescriptions(cls):
    g = G.for_class(cls)
    t = text({k: v for k, v in g.items() if k != "sources"})
    assert not NUMBER_WITH_UNIT.search(t), "a measurement or limit with a unit appeared"
    for banned in ("stop-drill", "doubler", "blend out", "within allowable limits:", "least severe", "approved repair:", "is airworthy", "safe to fly"):
        assert banned not in t.lower()
    assert all("VisionTra provides no" in g["repair_options"][0] or "no repair" in g["repair_options"][0].lower() for _ in [0])
    assert not any(re.match(r"(Eddy|Dye|Ultrasonic|Tap)", m) for m in g["inspection_methods"])


def test_proposal_never_reports_a_measurement_or_severity():
    p = PR.build(rec([DENT, ("crack", 0.6, (0.2, 0.2, 0.1, 0.1))]))
    t = text(p)
    assert not NUMBER_WITH_UNIT.search(t)
    assert not re.search(r"\b(severity|grade)\s*[:=]", t, re.I)


# ------------------------------------------------------------------ 11, 14, 15 through the API
def test_correction_history_and_report_keep_ai_and_reviewer_apart(api):
    u = user(api, "prop-a")
    b = create(api, aircraft_ref="VT-PRP", zone="")
    pid = add_fixture_predictions(b["id"], [("crack", 0.6, (0.2, 0.2, 0.1, 0.1))])[0]
    before = api.get(f"{URL}/{b['id']}").json()
    assert before["proposals"][pid]["stages"]["inspector"]["status"] == "not_reviewed"

    # 11. the reviewer corrects crack -> dent; the AI class and score stay on record
    assert decide(api, b["id"], "prediction", pid, "confirmed", kind="reclassify", corrected_class_id="dent", comment="looks like a dent").status_code in (200, 201)
    g = api.get(f"{URL}/{b['id']}").json()
    pr = g["proposals"][pid]
    assert pr["stages"]["inspector"]["status"] == "corrected" and pr["stages"]["inspector"]["class"] == "dent"
    assert pr["stages"]["inspector"]["ai_class"] == "crack" and pr["stages"]["ai"] == {**pr["stages"]["ai"], "class": "crack", "score": 0.6}
    assert pr["stages"]["inspector"]["by"]["name"] and pr["stages"]["inspector"]["at"]
    assert [p for p in g["predictions"] if p["id"] == pid][0]["class_id"] == "crack"       # the prediction row is unchanged

    # 14. retrieving the record again gives the same proposal; reading never writes
    again = api.get(f"{URL}/{b['id']}").json()
    assert again["proposals"] == g["proposals"] and again["revision"] == g["revision"]

    # 15. the report keeps the AI class next to the reviewer's class and states the disposition gate
    assert signoff(api, b["id"]).status_code == 200
    from pdf_assertions import pdf_text
    rep = api.post(f"{URL}/{b['id']}/reports").json()
    rep = rep.get("report") or rep
    flat = pdf_text(api.get(rep["download_url"]).content)
    assert "(AI: Crack)" in flat and "Engineering disposition: not established" in flat
    assert "Repair options" not in flat and "Stop-drilling" not in flat
    assert u


def test_pending_ai_proposal_is_labelled_in_the_report(api):
    user(api, "prop-b")
    b = create(api)
    pid = add_fixture_predictions(b["id"], [("dent", 0.4, (0.3, 0.3, 0.1, 0.1))])[0]
    assert decide(api, b["id"], "prediction", pid, "further_review").status_code in (200, 201)
    p = api.get(f"{URL}/{b['id']}").json()["proposals"][pid]
    assert p["stages"]["inspector"]["status"] == "further_review" and p["stages"]["inspector"]["class"] is None
    assert not p["stages"]["disposition"]["requirements"][0]["met"]


# ------------------------------------------------------------------ genuine model output through the engine
def test_real_detector_output_feeds_the_engine_without_inventing_values():
    if not (STABLE / "model.pt").is_file():
        pytest.skip("VT-DET-0002 bundle not present")
    manifest = ROOT / "docs/evidence/phase4/hard_cases_manifest.json"
    case = next((c for c in json.loads(manifest.read_text(encoding="utf-8"))["cases"] if c["case_id"] == "P4-I"), None) if manifest.is_file() else None
    img = ROOT / "data/objects" / case["object_key"] if case else None
    if img is None or not img.is_file():
        pytest.skip("P4-I original not available in this checkout")
    from visentra_api.inspection.analysis import Engine
    res = Engine("VT-DET-0002", STABLE).detect(img.read_bytes(), "P4-I")
    dets = res.output["detections"]
    assert [d["class_name"] for d in dets] == ["dent", "dent", "dent"]               # pinned by test_phase4_ai on 2026-10-24
    W, H = res.output["input"]["width"], res.output["input"]["height"]
    r = rec([(d["class_name"], d["score"], tuple(d["box_normalised"][k] for k in "xywh")) for d in dets], aircraft=None, component="other", zone=None)
    r["media"][0].update(width=W, height=H)
    # Reproduce the per-class configuration persisted by the production run.
    meta = json.loads((STABLE / "metadata.json").read_text())
    r["analysis"]["thresholds"] = {"score_per_class": meta["thresholds"]["score_threshold"],
                                    "review_per_class": meta["thresholds"]["review_threshold"]}
    props = PR.build(r)
    for k, d in enumerate(dets, 1):
        p = props[f"p{k}"]
        assert p["assessment"]["score"] == d["score"] and p["assessment"]["predicted_class"] == "dent"
        assert p["assessment"]["score_threshold"] == 0.025 and p["assessment"]["review_threshold"] == 0.05   # from the run snapshot
        assert p["stages"]["disposition"]["status"] == "not_established"
    assert "do not by themselves establish a cause such as hail" in " ".join(props["p1"]["observation"]["statements"])
