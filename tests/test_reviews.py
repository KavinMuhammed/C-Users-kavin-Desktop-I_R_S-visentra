"""Phase 11: persistent human review (EP-12…EP-16) — decisions, reviewer findings, history, sign-off, reopen, permissions,
concurrency, immutability of AI predictions, append-only history and persistence across a restart.

AI predictions used here are TEST FIXTURE rows (tests/p11_fixtures.py): no operational model exists to produce real ones.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from conftest import Api
from p11_fixtures import URL, add_fixture_predictions, create, decide, finding, jpeg, post, rev, signoff, user
from visentra_api.db import SessionLocal, engine
from visentra_api.main import app
from visentra_api.models import AuditEvent, DefectPrediction, ExperimentalPrediction, ExperimentalRun, FindingDecision, ImmutableRecordError, Inspection, now, uid
from visentra_api.security import limiter

REPO = Path(__file__).resolve().parents[1]


def err(r):
    return r.json()["error"]


@pytest.fixture()
def rv():
    limiter.reset()
    with TestClient(app) as c:
        a = Api(c)
        a.me = user(a)
        yield a


def other(prefix="p11o"):
    c = TestClient(app)
    c.__enter__()
    a = Api(c)
    a.me = user(a, prefix)
    return a


def team(owner: Api) -> str:
    return owner.post("/api/workspaces", json={"name": f"Hangar {uuid.uuid4().hex[:5]}"}).json()["id"]


def events(iid: str) -> list[FindingDecision]:
    with SessionLocal() as db:
        return db.scalars(select(FindingDecision).where(FindingDecision.inspection_id == iid).order_by(FindingDecision.inspection_revision)).all()


# ================================================================== reviewer findings and decisions
def test_reviewer_finding_is_persisted_attributed_and_moves_the_inspection_into_review(rv):
    insp = create(rv)
    assert insp["status"] == "awaiting_review" and insp["revision"] == 0 and insp["human_findings"] == []
    r = finding(rv, insp, "crack", comment="Hairline crack aft of fastener row 3")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["revision"] == 1 and r.headers["ETag"] == '"1"'
    f = b["finding"]
    assert f["category"] == "crack" and f["kind"] == "human_finding" and f["created_by"]["id"] == rv.me["user"]["id"]
    assert b["event"]["kind"] == "decision" and b["event"]["decision"] == "confirmed" and b["event"]["actor"]["id"] == rv.me["user"]["id"]
    g = rv.get(f"{URL}/{insp['id']}").json()
    assert g["status"] == "in_review" and g["revision"] == 1 and g["predictions"] == []
    assert [h["id"] for h in g["human_findings"]] == [f["id"]]
    cd = g["current_decisions"][f"human_finding:{f['id']}"]
    assert cd["decision"] == "confirmed" and cd["comment"] == "Hairline crack aft of fastener row 3" and cd["actor"]["id"] == rv.me["user"]["id"]
    assert g["counts"]["human_findings"] == 1 and g["counts"]["confirmed"] == 1 and g["categories"] == ["crack"]
    assert g["persistence"]["review"] is True and g["review_permissions"] == {"review": True, "report_create": True, "report_read": True}
    with SessionLocal() as db:
        ev = db.query(AuditEvent).filter(AuditEvent.action == "finding.created", AuditEvent.resource_id == insp["id"]).one()
        assert ev.meta["revision"] == 1 and ev.meta["category"] == "crack"


def test_decisions_on_a_reviewer_finding_change_state_but_never_rewrite_history(rv):
    insp = create(rv)
    fid = finding(rv, insp).json()["finding"]["id"]
    for d in ("further_review", "rejected", "confirmed"):
        r = decide(rv, insp["id"], "human_finding", fid, d, comment=f"now {d}")
        assert r.status_code == 200, r.text
        assert r.json()["current_decisions"][f"human_finding:{fid}"]["decision"] == d
    evs = events(insp["id"])
    assert [(e.kind, e.decision) for e in evs] == [("decision", "confirmed"), ("decision", "further_review"), ("decision", "rejected"), ("decision", "confirmed")]
    assert [e.inspection_revision for e in evs] == [1, 2, 3, 4]
    assert evs[2].previous == {"decision": "further_review", "corrected_class_id": None}
    h = rv.get(f"{URL}/{insp['id']}/decisions").json()
    assert h["total"] == 4 and h["current_revision"] == 4 and h["items"][1]["decision_label"] == "Needs further investigation"
    assert all(i["actor"]["name"] for i in h["items"])
    r = decide(rv, insp["id"], "human_finding", fid, "pending")
    assert r.status_code == 422 and err(r)["field"] == "decision"                 # a reviewer finding never goes back to pending
    r = decide(rv, insp["id"], "human_finding", fid, "confirmed")
    assert r.status_code == 409                                                     # unchanged decision is not a new event
    assert len(events(insp["id"])) == 4


def test_note_and_delete_are_events_and_deleted_findings_stay_in_history(rv):
    insp = create(rv)
    fid = finding(rv, insp).json()["finding"]["id"]
    assert decide(rv, insp["id"], "human_finding", fid, None, kind="note").status_code == 422      # a note needs text
    r = decide(rv, insp["id"], "human_finding", fid, None, kind="note", comment="Checked with a torch: paint edge only?")
    assert r.status_code == 200 and r.json()["current_decisions"][f"human_finding:{fid}"]["comment"].startswith("Checked")
    r = decide(rv, insp["id"], "human_finding", fid, None, kind="delete", comment="Paint edge, not a defect")
    assert r.status_code == 200
    g = rv.get(f"{URL}/{insp['id']}").json()
    assert g["human_findings"] == [] and g["counts"]["human_findings"] == 0
    assert [e.kind for e in events(insp["id"])] == ["decision", "note", "delete"]
    with SessionLocal() as db:
        row = db.execute(text("SELECT deleted_at, deleted_by FROM human_findings WHERE id = :i"), {"i": fid}).one()
        assert row.deleted_at is not None and row.deleted_by == rv.me["user"]["id"]
    assert decide(rv, insp["id"], "human_finding", fid, "rejected").status_code == 404        # gone from review, kept in history


# ================================================================== AI predictions (TEST FIXTURE rows)
def test_prediction_decisions_keep_the_ai_proposal_unchanged(rv):
    insp = create(rv)
    p1, p2 = add_fixture_predictions(insp["id"], [("dent", 0.81, (0.1, 0.1, 0.2, 0.2)), ("dent", 0.42, (0.5, 0.5, 0.1, 0.1))])
    with SessionLocal() as db:
        before = [tuple(r) for r in db.execute(text("SELECT * FROM defect_predictions ORDER BY ordinal")).all() if r.inspection_id == insp["id"]]
    g = rv.get(f"{URL}/{insp['id']}").json()
    assert [p["id"] for p in g["predictions"]] == [p1, p2] and g["counts"]["pending"] == 2
    assert g["predictions"][0]["kind"] == "ai_prediction" and g["predictions"][0]["model_version"] == "v3" and g["analysis"]["model"]["architecture"] == "TEST-FIXTURE-DET"
    assert decide(rv, insp["id"], "prediction", p1, "confirmed").status_code == 200
    r = decide(rv, insp["id"], "prediction", p2, None, kind="reclassify", corrected_class_id="corrosion", comment="Rust bloom, not a dent")
    assert r.status_code == 200
    cd = r.json()["current_decisions"][f"prediction:{p2}"]
    assert cd["decision"] == "confirmed" and cd["corrected_class_id"] == "corrosion"
    assert decide(rv, insp["id"], "prediction", p2, None, kind="reclassify", corrected_class_id="dent").status_code == 422   # same as predicted
    assert decide(rv, insp["id"], "prediction", p1, None, kind="delete").status_code == 422                                  # predictions are never deleted
    g = rv.get(f"{URL}/{insp['id']}").json()
    assert g["predictions"][1]["class_id"] == "dent" and g["predictions"][1]["score"] == 0.42                               # the AI proposal is intact
    assert sorted(g["categories"]) == ["corrosion", "dent"]
    with SessionLocal() as db:
        after = [tuple(r) for r in db.execute(text("SELECT * FROM defect_predictions ORDER BY ordinal")).all() if r.inspection_id == insp["id"]]
        assert after == before
        with pytest.raises((IntegrityError, OperationalError, ImmutableRecordError)):        # ORM guard + database trigger
            p = db.get(DefectPrediction, p1)
            p.score = 0.99
            db.commit()
        db.rollback()
    with engine.begin() as c, pytest.raises((IntegrityError, OperationalError)):
        c.execute(text("UPDATE defect_predictions SET class_id = 'crack' WHERE id = :i"), {"i": p1})


def test_targets_must_belong_to_this_inspection(rv):
    a, b_ = create(rv), create(rv)
    (pa,) = add_fixture_predictions(a["id"], [("dent", 0.7, (0.1, 0.1, 0.2, 0.2))])
    fb = finding(rv, b_).json()["finding"]["id"]
    for ttype, tid in (("prediction", pa), ("human_finding", fb)):
        r = decide(rv, b_["id"] if ttype == "prediction" else a["id"], ttype, tid, "rejected")
        assert r.status_code == 404 and err(r)["field"] == "target.id", r.text
    r = post(rv, a["id"], "findings", {"media_id": b_["media"][0]["id"], "category": "dent", "box": {"x": .1, "y": .1, "w": .1, "h": .1}}, rev(rv, a["id"]))
    assert r.status_code == 404 and err(r)["field"] == "media_id"
    r = decide(rv, "f" * 32, "prediction", pa, "rejected", revision=0)
    assert r.status_code == 404 and err(r)["code"] == "INSPECTION_NOT_FOUND"
    with engine.begin() as c, pytest.raises(IntegrityError):         # the database refuses a cross-inspection decision too
        c.execute(text("INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, actor_id, inspection_revision) "
                       "VALUES (:id, :i, 'prediction', :p, 'decision', 'rejected', :u, 99)"), {"id": uid(), "i": b_["id"], "p": pa, "u": rv.me["user"]["id"]})


def test_experimental_suggestions_are_not_review_targets(rv):
    insp = create(rv)
    with SessionLocal() as db:
        run = ExperimentalRun(id=uid(), user_id=rv.me["user"]["id"], workspace_id=insp["workspace"]["id"], inspection_id=insp["id"], media_id=insp["media"][0]["id"],
                              source="inspection_media", model_id="VT-DET-0001", model_version="0.1.0", readiness="EVALUATED_LIMITED", status="completed",
                              outcome="detections", prediction_count=1, settings={}, timing_ms={})
        db.add(run); db.flush()
        ep = ExperimentalPrediction(run_id=run.id, ordinal=1, class_id=0, class_name="dent", score=0.3, x_min=1, y_min=1, x_max=50, y_max=50)
        db.add(ep); db.commit()
        epid = ep.id
    r = decide(rv, insp["id"], "prediction", epid, "confirmed")
    assert r.status_code == 404                                      # ADR-014: an experimental suggestion never becomes a reviewable prediction
    g = rv.get(f"{URL}/{insp['id']}").json()
    assert g["status"] == "awaiting_review" and g["revision"] == 0 and g["predictions"] == []


# ================================================================== validation
@pytest.mark.parametrize("body,field", [
    ({"target": {"type": "human_finding", "id": "FID"}, "kind": "decision", "decision": "approve"}, "decision"),
    ({"target": {"type": "defect", "id": "x"}, "decision": "confirmed"}, "target"),
    ({"target": "nope", "decision": "confirmed"}, "target"),
    ({"target": {"type": "human_finding", "id": "x" * 65}, "decision": "confirmed"}, "target"),
    ({"target": {"type": "human_finding", "id": "FID"}, "kind": "approve"}, "kind"),
    ({"target": {"type": "human_finding", "id": "FID"}, "decision": "rejected", "comment": "x" * 601}, "comment"),
    ({"target": {"type": "human_finding", "id": "FID"}, "decision": "rejected", "comment": 5}, "comment"),
    ({"target": {"type": "human_finding", "id": "FID"}, "kind": "reclassify", "corrected_class_id": "corrosion"}, "kind"),
    ({"target": {"type": "human_finding", "id": "FID"}, "decision": "rejected", "corrected_class_id": "crack"}, "corrected_class_id"),
])
def test_invalid_decision_requests_are_rejected(rv, body, field):
    insp = create(rv)
    fid = finding(rv, insp).json()["finding"]["id"]
    body = json.loads(json.dumps(body).replace("FID", fid))
    n = len(events(insp["id"]))
    r = post(rv, insp["id"], "decisions", body, rev(rv, insp["id"]))
    assert r.status_code == 422 and err(r)["field"] == field, r.text
    assert len(events(insp["id"])) == n


@pytest.mark.parametrize("box", [{"x": -0.1, "y": 0, "w": .2, "h": .2}, {"x": .9, "y": 0, "w": .2, "h": .2}, {"x": 0, "y": 0, "w": 0, "h": .1}, {"x": "a"}, None,
                                 {"x": float("nan"), "y": 0, "w": .1, "h": .1}])
def test_finding_box_and_category_are_validated(rv, box):
    insp = create(rv)
    body = {"media_id": insp["media"][0]["id"], "category": "dent", "box": box}
    r = rv.c.post(f"{URL}/{insp['id']}/findings", content=json.dumps(body, allow_nan=True), headers={"X-CSRF-Token": rv.csrf, "If-Match": "0", "Content-Type": "application/json"})
    assert r.status_code == 422 and err(r)["field"] == "box", r.text
    r = post(rv, insp["id"], "findings", {**body, "box": {"x": .1, "y": .1, "w": .1, "h": .1}, "category": "scratch"}, 0)
    assert r.status_code == 422 and err(r)["field"] == "category"


def test_comments_are_stored_as_plain_text(rv):
    insp = create(rv)
    hostile = "<img src=x onerror=alert(1)> \x00\x07 ünïcödé – “quotes”"
    r = finding(rv, insp, comment=hostile)
    assert r.status_code == 201
    c = rv.get(f"{URL}/{insp['id']}/decisions").json()["items"][0]["comment"]
    assert c == "<img src=x onerror=alert(1)>  ünïcödé – “quotes”"            # control characters removed; markup kept as inert text


# ================================================================== revision, conflicts, idempotency
def test_stale_or_missing_revision_writes_nothing(rv):
    insp = create(rv)
    fid = finding(rv, insp).json()["finding"]["id"]
    r = rv.post(f"{URL}/{insp['id']}/decisions", json={"target": {"type": "human_finding", "id": fid}, "decision": "rejected"})
    assert r.status_code == 428 and err(r)["code"] == "REVISION_REQUIRED"
    r = decide(rv, insp["id"], "human_finding", fid, "rejected", revision=0)
    assert r.status_code == 409 and err(r)["code"] == "REVIEW_CONFLICT" and err(r)["details"]["current_revision"] == 1
    assert len(events(insp["id"])) == 1


def test_concurrent_reviewers_one_wins_the_others_conflict(rv):
    insp = create(rv)
    fid = finding(rv, insp).json()["finding"]["id"]
    with ThreadPoolExecutor(5) as ex:
        rs = list(ex.map(lambda d: decide(rv, insp["id"], "human_finding", fid, d, revision=1), ["rejected", "further_review", "rejected", "further_review", "rejected"]))
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409, 409], [r.text for r in rs]
    evs = events(insp["id"])
    assert len(evs) == 2 and evs[1].inspection_revision == 2


def test_idempotency_key_replays_a_review_action(rv):
    insp = create(rv)
    body = {"media_id": insp["media"][0]["id"], "category": "dent", "box": {"x": .1, "y": .1, "w": .1, "h": .1}}
    k = f"rv-{uuid.uuid4().hex}"
    a = post(rv, insp["id"], "findings", body, 0, key=k)
    b = post(rv, insp["id"], "findings", body, 0, key=k)                           # retry after a lost answer: same request, same key
    assert a.status_code == b.status_code == 201 and b.headers.get("Idempotent-Replay") == "true" and a.json()["finding"]["id"] == b.json()["finding"]["id"]
    assert len(events(insp["id"])) == 1
    c = post(rv, insp["id"], "findings", {**body, "category": "crack"}, 0, key=k)
    assert c.status_code == 409 and err(c)["code"] == "IDEMPOTENCY_CONFLICT"


# ================================================================== sign-off and reopen
def test_signoff_requires_every_prediction_decided_and_records_the_outcome(rv):
    insp = create(rv)
    (p1,) = add_fixture_predictions(insp["id"], [("dent", 0.6, (0.3, 0.3, 0.1, 0.1))])
    r = signoff(rv, insp["id"])
    assert r.status_code == 409 and err(r)["code"] == "REVIEW_INCOMPLETE" and err(r)["details"]["pending_finding_ids"] == [p1]
    decide(rv, insp["id"], "prediction", p1, "rejected", comment="Rivet head reflection")
    r = signoff(rv, insp["id"], note="Panel inspected under raking light")
    assert r.status_code == 200, r.text
    g = r.json()["inspection"]
    assert g["status"] == "reviewed" and g["signoff_outcome"] == "no_confirmed_findings" and g["signed_off_by"]["id"] == rv.me["user"]["id"]
    assert g["signoff_note"] == "Panel inspected under raking light" and g["signed_off_at"]
    assert events(insp["id"])[-1].kind == "signoff"
    assert signoff(rv, insp["id"]).status_code == 409                              # already signed off


def test_signoff_with_nothing_recorded_and_reopen_on_change(rv):
    insp = create(rv)
    r = signoff(rv, insp["id"])                                                     # awaiting_review -> in_review -> reviewed
    assert r.status_code == 200 and r.json()["inspection"]["status"] == "reviewed" and r.json()["inspection"]["signoff_outcome"] == "no_confirmed_findings"
    r = finding(rv, insp, "corrosion")                                              # any change after sign-off reopens the review
    assert r.status_code == 201
    g = r.json()["inspection"]
    assert g["status"] == "in_review" and g["signed_off_at"] is None and g["signoff_outcome"] is None
    assert signoff(rv, insp["id"]).json()["inspection"]["signoff_outcome"] == "findings_recorded"
    r = post(rv, insp["id"], "reopen", {"comment": "Second opinion requested"}, rev(rv, insp["id"]))
    assert r.status_code == 200 and r.json()["inspection"]["status"] == "in_review"
    kinds = [e.kind for e in events(insp["id"])]
    assert kinds == ["signoff", "decision", "signoff", "reopen"]
    reopen_ev = events(insp["id"])[-1]
    assert reopen_ev.previous["outcome"] == "findings_recorded" and reopen_ev.comment == "Second opinion requested"
    assert post(rv, insp["id"], "reopen", {}, rev(rv, insp["id"])).status_code == 409   # only a signed-off inspection can be reopened


# ================================================================== permissions
@pytest.mark.parametrize("role,can_review", [("VIEWER", False), ("ML_ENGINEER", False), ("REVIEWER", True), ("OPERATOR", True), ("ADMIN", True)])
def test_review_permissions_follow_the_workspace_role(rv, role, can_review):
    ws = team(rv)
    insp = create(rv, workspace_id=ws)
    o = other()
    try:
        assert rv.post(f"/api/workspaces/{ws}/members", json={"email": o.me["user"]["email"], "role": role}).status_code == 201
        g = o.get(f"{URL}/{insp['id']}").json()
        assert g["review_permissions"]["review"] is can_review
        r = finding(o, insp)
        assert r.status_code == (201 if can_review else 403), r.text
        assert o.get(f"{URL}/{insp['id']}/decisions").status_code == 200        # everyone who can read can see the history
        if can_review:
            assert r.json()["event"]["actor"]["id"] == o.me["user"]["id"]          # attributed to the signed-in reviewer, never a request field
    finally:
        o.c.__exit__(None, None, None)


def test_reviewer_identity_cannot_be_supplied_by_the_client(rv):
    insp = create(rv)
    body = {"media_id": insp["media"][0]["id"], "category": "dent", "box": {"x": .1, "y": .1, "w": .1, "h": .1}, "actor_id": "someone-else", "created_by": "x"}
    r = post(rv, insp["id"], "findings", body, 0)
    assert r.status_code == 201 and r.json()["event"]["actor"]["id"] == rv.me["user"]["id"] and r.json()["finding"]["created_by"]["id"] == rv.me["user"]["id"]


def test_other_workspaces_and_anonymous_callers_are_refused(rv):
    insp = create(rv)
    o = other()
    try:
        for path, body in (("decisions", {"target": {"type": "human_finding", "id": "x"}, "decision": "rejected"}), ("findings", {}), ("signoff", {}), ("reopen", {})):
            r = post(o, insp["id"], path, body, 0)
            assert r.status_code == 404 and err(r)["code"] == "INSPECTION_NOT_FOUND", path
        assert o.get(f"{URL}/{insp['id']}/decisions").status_code == 404
    finally:
        o.c.__exit__(None, None, None)
    assert TestClient(app).get(f"{URL}/{insp['id']}/decisions").status_code == 401
    r = rv.c.post(f"{URL}/{insp['id']}/signoff", json={}, headers={"If-Match": "0"})            # no CSRF token
    assert r.status_code == 403 and err(r)["code"] == "CSRF"


# ================================================================== append-only history and restart
def test_review_events_are_append_only_in_the_database(rv):
    insp = create(rv)
    finding(rv, insp)
    eid = events(insp["id"])[0].id
    for sql in ("UPDATE finding_decisions SET decision = 'rejected' WHERE id = :i", "DELETE FROM finding_decisions WHERE id = :i"):
        with engine.begin() as c, pytest.raises((IntegrityError, OperationalError)):
            c.execute(text(sql), {"i": eid})
    with SessionLocal() as db, pytest.raises(ImmutableRecordError):
        e = db.get(FindingDecision, eid)
        e.comment = "rewritten"
        db.commit()
    assert events(insp["id"])[0].decision == "confirmed"


def test_review_survives_an_application_restart(rv):
    insp = create(rv)
    fid = finding(rv, insp, "dent", comment="persist me").json()["finding"]["id"]
    decide(rv, insp["id"], "human_finding", fid, "further_review")
    engine.dispose()
    code = ("from visentra_api.db import SessionLocal\nfrom visentra_api.models import Inspection, FindingDecision\nimport json, sys\n"
            "db = SessionLocal(); i = db.get(Inspection, sys.argv[1])\n"
            "ev = db.query(FindingDecision).filter_by(inspection_id=i.id).order_by(FindingDecision.inspection_revision).all()\n"
            "print(json.dumps({'status': i.status, 'revision': i.revision, 'events': [[e.kind, e.decision, e.comment] for e in ev]}))\n")
    env = {**os.environ, "PYTHONPATH": f"{REPO / 'apps/api'}{os.pathsep}{REPO / 'ml'}"}
    p = subprocess.run([sys.executable, "-c", code, insp["id"]], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == {"status": "in_review", "revision": 2,
                                                             "events": [["decision", "confirmed", "persist me"], ["decision", "further_review", None]]}
    with TestClient(app) as c2:
        c2.cookies.update(rv.c.cookies)
        g = c2.get(f"{URL}/{insp['id']}").json()
        assert g["current_decisions"][f"human_finding:{fid}"]["decision"] == "further_review"


def test_history_filter_by_confirmed_category(rv):
    a, b_ = create(rv, zone="cat-a"), create(rv, zone="cat-b")
    finding(rv, a, "corrosion")
    fb = finding(rv, b_, "corrosion").json()["finding"]["id"]
    decide(rv, b_["id"], "human_finding", fb, "rejected")
    ids = [i["id"] for i in rv.get(URL, params={"category": "corrosion"}).json()["items"]]
    assert ids == [a["id"]]                                                          # rejected findings do not count
    item = next(i for i in rv.get(URL).json()["items"] if i["id"] == b_["id"])
    assert item["counts"]["rejected"] == 1 and item["categories"] == []
