"""AI recovery: three-state relevance gate, guidance caveats, device-safe evaluation, reproducible threaded loading."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))


def _gate(uncertain=None):
    from visentra_ml.detection.domain_gate import Gate
    g = Gate.__new__(Gate)                      # no weights needed for the decision policy
    g.threshold, g.airplane_min = 0.77, 0.5
    g.spec = {"uncertain": uncertain} if uncertain else {}
    return g


def ev(p=0.0, air=0.0, pc=0.0, ac=0.0, top="donut", ts=0.6):
    return {"p": p, "airplane": air, "p_crop_max": pc, "airplane_crop_max": ac, "coco_top": top, "coco_top_score": ts}


def test_gate_v1_policy_unchanged_without_band():
    g = _gate()
    assert g.decide(ev(p=0.9))[0] == "relevant"
    assert g.decide(ev(air=0.6))[0] == "relevant"
    assert g.decide(ev(p=0.3, pc=0.95))[0] == "irrelevant"          # crops are only used by a three-state gate
    st, why = g.decide(ev())
    assert st == "irrelevant" and "donut" not in why   # Phase 20: a generic COCO guess is never the reason


def test_gate_three_state():
    g = _gate({"p_min": 0.2, "airplane_min": 0.3})
    assert g.decide(ev(p=0.9))[0] == "relevant"
    assert g.decide(ev(p=0.01, pc=0.8))[0] == "relevant"            # close-up recognised in a crop
    assert g.decide(ev(p=0.0003, ac=0.48))[0] == "uncertain"        # the hail-nose incident: analysed, flagged
    assert g.decide(ev(p=0.25))[0] == "uncertain"
    assert g.decide(ev(p=0.01, air=0.05, pc=0.1, ac=0.1))[0] == "irrelevant"


def test_guidance_has_caveats():
    from visentra_api.inspection import guidance as GD
    for c in ("dent", "crack", "corrosion"):
        g = GD.for_class(c)
        assert g["lookalikes"] and g["evidence_quality"] and g["unknowns"] and "not that the defect is small" in g["low_confidence"]
    assert GD.for_class("unknown-class")["class"] == "other"


def test_per_sample_seed_is_order_independent():
    torch = pytest.importorskip("torch")  # noqa: F841
    from visentra_ml.detection import data as Dt

    class Fake(Dt.DetectionDataset):
        def load(self, i):
            import numpy as np
            rs = np.random.RandomState(i)
            return rs.randint(0, 255, (60, 80, 3)).astype("uint8"), np.array([[0, 10, 10, 50, 40]], dtype="float32")
    aug = {"scale": [0.8, 1.2], "translate": 0.1, "hflip": 0.5, "vflip": 0.1}
    ds = Fake([type("I", (), {"file_id": f"f{i}"})() for i in range(4)], (64, 64), augment=aug)
    a = [ds.sample(i, 1000 + i) for i in range(4)]
    b = [ds.sample(i, 1000 + i) for i in reversed(range(4))][::-1]
    for (x, t, _), (y, u, _) in zip(a, b):
        assert (x == y).all() and (t == u).all()
    random.seed(0)


def test_predict_items_moves_inputs_to_model_device():
    torch = pytest.importorskip("torch")
    from visentra_ml.detection import engine as E
    src = Path(E.__file__).read_text()
    assert "imgs.to(dev)" in src and "next(model.parameters()).device" in src


# ---------------------------------------------------------------- API: an uncertain image is analysed and flagged
from test_phase15 import INCIDENT_VERDICT, StubGate  # noqa: E402
from test_phase13 import model_dir  # noqa: E402,F401


@pytest.fixture()
def uncertain_gate(model_dir, monkeypatch):  # noqa: F811
    from visentra_api.inspection import analysis as AN
    v = {**INCIDENT_VERDICT, "gate_id": "VT-GATE-0002", "decision": "uncertain", "in_domain": True, "uncertain": True,
         "p_aircraft_surface_crops": 0.1905, "coco_airplane_score_crops": 0.4818,
         "reason": "possibly an aircraft surface: analysed, but please confirm the image is relevant"}
    monkeypatch.setattr(AN, "gate", lambda: (StubGate(v), None))
    yield


def test_uncertain_image_is_analysed_not_discarded(api, uncertain_gate):
    from p11_fixtures import create, user
    user(api, "unc-a")
    b = create(api)
    d = b["detection"]
    assert d["state"] in ("completed_candidates", "completed_zero") and d["completed"] is True
    assert d["relevance"] == "uncertain" and d["relevance_uncertain"] is True and "confirm" in d["relevance_note"]
    assert b["analysis"] is not None and b["analysis"]["domain_gate"]["decision"] == "uncertain"


def test_rejected_image_reports_irrelevant(api, model_dir, monkeypatch):  # noqa: F811
    from p11_fixtures import create, user
    from visentra_api.inspection import analysis as AN
    monkeypatch.setattr(AN, "gate", lambda: (StubGate(INCIDENT_VERDICT), None))
    user(api, "unc-b")
    d = create(api)["detection"]
    assert d["state"] == "relevance_rejected" and d["relevance"] == "irrelevant" and d["relevance_uncertain"] is False
