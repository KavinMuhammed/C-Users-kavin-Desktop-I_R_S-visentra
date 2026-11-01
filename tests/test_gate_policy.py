"""VT-GATE-0002 decision policy (Phase 20). Uses the recorded evidence of the INS-2026-000017 / INS-0004 A319 hail-nose
upload (p 0.0003, best crop 0.1905, airplane in a crop 0.4818, COCO top 'donut' 0.7182) without the image itself."""
from types import SimpleNamespace

from visentra_ml.detection.domain_gate import Gate

SPEC = {"uncertain": {"p_min": 0.35, "airplane_min": 0.4}}


def gate():
    g = Gate.__new__(Gate)
    g.spec, g.threshold, g.airplane_min = SPEC, 0.77, 0.5
    return g


A319 = {"p": 0.0003, "airplane": 0.0023, "p_crop_max": 0.1905, "airplane_crop_max": 0.4818, "coco_top": "donut", "coco_top_score": 0.7182}


def test_a319_hail_nose_is_uncertain_not_rejected():
    state, reason = gate().decide(A319)
    assert state == "uncertain"


def test_generic_coco_label_is_never_the_rejection_reason():
    ev = dict(A319, airplane_crop_max=0.01)
    state, reason = gate().decide(ev)
    assert state == "irrelevant"
    assert "donut" not in reason and "looks like" not in reason


def test_relevant_paths():
    g = gate()
    assert g.decide(dict(A319, p=0.9))[0] == "relevant"
    assert g.decide(dict(A319, airplane=0.6))[0] == "relevant"
    assert g.decide(dict(A319, p_crop_max=0.8))[0] == "relevant"
