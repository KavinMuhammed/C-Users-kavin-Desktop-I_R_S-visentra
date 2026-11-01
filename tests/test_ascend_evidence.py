"""Handoff contract tests; fixture predictions prove software behaviour, not detector quality."""
import hashlib

from p11_fixtures import URL, add_fixture_predictions, create, decide, signoff, user
from test_proposal_engine import rec, DENT
from visentra_api.inspection import evidence, proposal


def test_historical_thresholds_do_not_follow_bundle_changes(monkeypatch):
    out = rec([DENT])
    out["analysis"]["thresholds"] = {"score_per_class": {"dent": 0.123}, "review_per_class": {"dent": 0.456}}
    monkeypatch.setattr(proposal, "_bundle_json", lambda *a: {"thresholds": {"score_threshold": {"dent": .99}}})
    result = proposal.build(out)["p1"]["assessment"]
    assert result["score_threshold"] == .123
    assert result["review_threshold"] == .456
    out["analysis"]["thresholds"] = {"score": .025, "review": .05}
    result = proposal.build(out)["p1"]["assessment"]
    assert result["score_threshold"] is None and result["review_threshold"] is None


def test_export_keeps_prediction_correction_history_and_hash(api, api2):
    user(api, "ascend")
    insp = create(api)
    pid = add_fixture_predictions(insp["id"], [("crack", .7, (.1, .2, .3, .4))])[0]
    assert decide(api, insp["id"], "prediction", pid, kind="reclassify", corrected_class_id="dent", comment="Fixture correction").status_code == 200
    assert signoff(api, insp["id"]).status_code == 200
    path = f"{URL}/{insp['id']}/evidence"
    before = api.get(f"{URL}/{insp['id']}").json()
    response = api.get(path)
    assert response.status_code == 200
    packet = response.json(); payload = packet["payload"]
    assert payload["original_predictions"][0]["class_id"] == "crack"
    assert payload["original_predictions"][0]["score"] == .7
    assert payload["current_decisions"][f"prediction:{pid}"]["corrected_class_id"] == "dent"
    assert [e["kind"] for e in payload["review_events"]] == ["reclassify", "signoff"]
    assert payload["analysis_runs"][0]["model"]["weights_sha256"] == "c" * 64
    assert packet["integrity"]["sha256"] == hashlib.sha256(evidence.canonical_bytes(payload)).hexdigest()
    assert api.get(path).json() == packet
    assert api.get(f"{URL}/{insp['id']}").json() == before
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    checks = {c["id"]: c["recorded"] for c in payload["evidence_checklist"]["checks"]}
    assert checks["review_decisions"] and not checks["aircraft_identity"] and not checks["engineering_disposition"]
    payload["original_predictions"][0]["score"] = .8
    assert packet["integrity"]["sha256"] != hashlib.sha256(evidence.canonical_bytes(payload)).hexdigest()
    user(api2, "ascend-outsider")
    assert api2.get(path).status_code == 404


def test_manual_empty_export_does_not_imply_coverage_or_completed_review(api):
    user(api, "ascend-empty")
    insp = create(api)
    payload = api.get(f"{URL}/{insp['id']}/evidence").json()["payload"]
    assert payload["original_predictions"] == payload["analysis_runs"] == []
    assert not next(c for c in payload["evidence_checklist"]["checks"] if c["id"] == "review_decisions")["recorded"]
    assert any("coverage" in x for x in payload["limitations"])


def test_export_requires_authentication(api):
    assert api.get(f"{URL}/unknown/evidence").status_code == 401
