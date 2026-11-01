"""Phase 15: truthful detection / review states (incident INC-15-01).

Incident (2026-10-18, user's machine): an airliner nose photo (dk-comp-dented-plane.webp, sha256 61db28bd…) was
rejected by VT-GATE-0001 (p_aircraft_surface 0.0003, COCO top class "donut" 0.718). The rejected run was stored
with status 'succeeded', the inspection showed "No defects detected above the model's thresholds" and sign-off was
offered as if the detector had looked at the image. These tests pin the corrected behaviour with a stub gate that
returns exactly the verdict recorded on that machine (the photo itself is not redistributed).
"""
from __future__ import annotations

import pytest

from p11_fixtures import create, finding, rev, signoff, user
from test_phase13 import model_dir  # noqa: F401 — fixture: a packaged random-weights detector
from visentra_api.db import SessionLocal
from visentra_api.inspection import analysis as AN
from visentra_api.models import AnalysisRun, AuditEvent

URL = "/api/v1/inspections"
INCIDENT_VERDICT = {"gate_id": "VT-GATE-0001", "in_domain": False, "p_aircraft_surface": 0.0003, "threshold": 0.77, "coco_airplane_score": 0.0023,
                    "coco_top_class": "donut", "coco_top_score": 0.7182, "reason": "not an aircraft surface (looks like: donut)", "state": "ready", "ms": 1.0}


class StubGate:
    def __init__(self, verdict):
        self.verdict = verdict

    def check_bytes(self, data):
        return dict(self.verdict)

    def describe(self):
        return {"gate_id": "VT-GATE-STUB", "state": "ready"}


@pytest.fixture()
def rejecting_gate(model_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(AN, "gate", lambda: (StubGate(INCIDENT_VERDICT), None))
    yield


@pytest.fixture()
def accepting_gate(model_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(AN, "gate", lambda: (StubGate({**INCIDENT_VERDICT, "in_domain": True, "p_aircraft_surface": 0.99, "reason": "aircraft surface"}), None))
    yield


def _rerun(api, iid, **body):
    return api.post(f"{URL}/{iid}/analyses", json=body, headers={"If-Match": f'"{rev(api, iid)}"'})


def test_gate_rejection_is_not_a_completed_analysis(api, rejecting_gate):
    user(api, "inc-a")
    b = create(api)
    d = b["detection"]
    assert d["state"] == "relevance_rejected" and d["completed"] is False and d["gate"]["coco_top_class"] == "donut"
    assert "rejected" in d["label"] and "no candidates" not in d["label"].lower()
    assert b["analysis"] is None and b["predictions"] == [] and b["review_state"]["state"] == "not_reviewed"
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter_by(inspection_id=b["id"]).one()
        assert run.status == "cancelled" and run.error_code == "OUT_OF_DOMAIN" and "donut" in run.error_message


def test_signoff_after_gate_rejection_needs_the_manual_only_acknowledgement(api, rejecting_gate):
    user(api, "inc-b")
    b = create(api)
    assert b["signoff_requires"] == "manual_only"
    r = signoff(api, b["id"], ack=None)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVIEW_INCOMPLETE"
    assert r.json()["error"]["details"].get("required_acknowledgement", r.json()["error"].get("required_acknowledgement")) in ("manual_only", None)
    assert signoff(api, b["id"], ack="visual_check").status_code == 409            # the wrong acknowledgement is refused too
    r = signoff(api, b["id"], ack="manual_only")
    assert r.status_code == 200 and r.json()["inspection"]["status"] == "reviewed"
    with SessionLocal() as db:
        a = db.query(AuditEvent).filter_by(action="inspection.signed_off", resource_id=b["id"]).one()
        assert a.meta["detection_state"] == "relevance_rejected" and a.meta["acknowledgement"] == "manual_only"


def test_override_requires_a_reason_is_audited_and_is_kept_on_later_reruns(api, rejecting_gate):
    user(api, "inc-c")
    b = create(api)
    assert _rerun(api, b["id"], force=True).status_code == 422
    assert _rerun(api, b["id"], force=True, reason="  ").status_code == 422
    r = _rerun(api, b["id"], force=True, reason="Radome close-up: hail damage")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["detection"]["state"] in ("completed_candidates", "completed_zero") and j["detection"]["overridden"] is True
    assert j["analysis"]["domain_gate"]["override_reason"] == "Radome close-up: hail damage"
    # a plain re-run keeps the engineer's override instead of silently reverting to "rejected"
    r2 = _rerun(api, b["id"])
    assert r2.status_code == 201 and r2.json()["detection"]["state"] != "relevance_rejected"
    assert r2.json()["analysis"]["domain_gate"]["override_inherited"] is True


def test_zero_candidates_requires_a_visual_check_acknowledgement(api, accepting_gate, monkeypatch):
    user(api, "inc-d")
    real = AN.Engine.detect

    def no_boxes(self, data, ref):
        res = real(self, data, ref)
        res.output = {**res.output, "detections": []}
        return res
    monkeypatch.setattr(AN.Engine, "detect", no_boxes)
    b = create(api)
    assert b["detection"]["state"] == "completed_zero" and b["detection"]["completed"] is True
    assert "not" not in b["detection"]["label"].lower().split("no candidates")[0]  # wording: "Detection ran: no candidates …"
    assert b["signoff_requires"] == "visual_check"
    assert signoff(api, b["id"], ack=None).status_code == 409
    assert signoff(api, b["id"], ack="visual_check").status_code == 200


def test_a_recorded_finding_replaces_the_visual_check(api, accepting_gate, monkeypatch):
    user(api, "inc-e")
    real = AN.Engine.detect
    monkeypatch.setattr(AN.Engine, "detect", lambda self, data, ref: (lambda r: (setattr(r, "output", {**r.output, "detections": []}), r)[1])(real(self, data, ref)))
    b = create(api)
    assert finding(api, b, "dent").status_code == 201
    g = api.get(f"{URL}/{b['id']}").json()
    assert g["signoff_requires"] is None


def test_legacy_rows_marked_succeeded_but_out_of_domain_are_read_as_rejections(api, rejecting_gate):
    user(api, "inc-f")
    b = create(api)
    with SessionLocal() as db:                                   # what a Phase 13 database contains
        run = db.query(AnalysisRun).filter_by(inspection_id=b["id"]).one()
        run.status, run.error_code, run.error_message = "succeeded", None, None
        db.commit()
    g = api.get(f"{URL}/{b['id']}").json()
    assert g["detection"]["state"] == "relevance_rejected" and g["analysis"] is None and g["signoff_requires"] == "manual_only"


def test_report_of_a_gate_rejected_manual_only_inspection_says_so(api, rejecting_gate):
    from pdf_assertions import pdf_text
    user(api, "inc-g")
    b = create(api)
    assert finding(api, b, "dent").status_code == 201
    assert signoff(api, b["id"], ack="manual_only").status_code == 200
    r = api.post(f"{URL}/{b['id']}/reports")
    assert r.status_code in (200, 201), r.text
    rep = r.json().get("report") or r.json()
    pdf = api.get(rep["download_url"]).content
    flat = pdf_text(pdf)
    assert "Not analysed" in flat and "donut" in flat and "manual-only inspection" in flat.lower()
    assert "Acknowledgement" in flat and "Manual-only inspection" in flat


# ------------------------------------------------------------------ bulk decision (root cause of the "Reject the rest" e2e failure)
def test_bulk_reject_is_one_request_with_per_target_events(api):
    from p11_fixtures import add_fixture_predictions, decide
    user(api, "bulk-a")
    b = create(api)
    ids = add_fixture_predictions(b["id"], [("crack", 0.5, (0.1, 0.1, 0.1, 0.1)), ("dent", 0.4, (0.3, 0.3, 0.1, 0.1)), ("corrosion", 0.6, (0.5, 0.5, 0.1, 0.1)),
                                             ("dent", 0.3, (0.7, 0.7, 0.1, 0.1))])
    assert decide(api, b["id"], "prediction", ids[0], "confirmed").status_code == 200
    r0 = rev(api, b["id"])
    r = api.post(f"{URL}/{b['id']}/decisions/bulk", json={"decision": "rejected", "targets": ids[1:]}, headers={"If-Match": f'"{r0}"'})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["revision"] == r0 + 3 and j["decided"] == 3
    cur = j["inspection"]["current_decisions"]
    assert [cur[f"prediction:{i}"]["decision"] for i in ids] == ["confirmed", "rejected", "rejected", "rejected"]
    assert j["inspection"]["signoff_requires"] is None
    with SessionLocal() as db:
        n = db.query(AuditEvent).filter_by(action="finding.decided", resource_id=b["id"]).count()
        assert n == 4
    # refreshed state keeps the decisions
    g = api.get(f"{URL}/{b['id']}").json()
    assert sum(1 for k, v in g["current_decisions"].items() if v.get("decision") == "rejected") == 3


def test_bulk_refuses_decided_or_foreign_targets_atomically(api):
    from p11_fixtures import add_fixture_predictions, decide
    user(api, "bulk-b")
    b = create(api)
    ids = add_fixture_predictions(b["id"], [("crack", 0.5, (0.1, 0.1, 0.1, 0.1)), ("dent", 0.4, (0.3, 0.3, 0.1, 0.1))])
    assert decide(api, b["id"], "prediction", ids[0], "confirmed").status_code == 200
    h = {"If-Match": f'"{rev(api, b["id"])}"'}
    assert api.post(f"{URL}/{b['id']}/decisions/bulk", json={"decision": "rejected", "targets": ids}, headers=h).status_code == 409
    assert api.post(f"{URL}/{b['id']}/decisions/bulk", json={"decision": "rejected", "targets": [ids[1], "f" * 32]}, headers=h).status_code == 404
    assert api.post(f"{URL}/{b['id']}/decisions/bulk", json={"decision": "pending", "targets": [ids[1]]}, headers=h).status_code == 422
    g = api.get(f"{URL}/{b['id']}").json()
    assert g["current_decisions"].get(f"prediction:{ids[1]}", {}).get("decision", "pending") == "pending"     # nothing half-applied


# ------------------------------------------------------------------ Phase 16: maintenance guidance
def test_defect_guidance_covers_the_three_classes_with_sources(api):
    user(api, "guide-a")
    r = api.get("/api/v1/defect-guidance")
    assert r.status_code == 200
    g = r.json()
    for c in ("dent", "crack", "corrosion"):
        x = g["classes"][c]
        assert x["immediate_actions"] and x["repair_options"] and x["prevention"] and x["sources"]
        assert "SRM" in x["disclaimer"]
    assert api.c.get("/api/v1/defect-guidance").status_code in (200, 401)


def test_guidance_requires_sign_in(api2):
    assert api2.c.get("/api/v1/defect-guidance").status_code == 401


def test_report_includes_guidance_for_confirmed_classes(api, rejecting_gate):
    from pdf_assertions import pdf_text
    user(api, "guide-b")
    b = create(api)
    assert finding(api, b, "corrosion").status_code == 201
    assert signoff(api, b["id"], ack="manual_only").status_code == 200
    rep = api.post(f"{URL}/{b['id']}/reports").json()
    rep = rep.get("report") or rep
    flat = pdf_text(api.get(rep["download_url"]).content)
    assert "Maintenance guidance" in flat and "Prevention" in flat and "AC 43-4B" in flat
