"""Model readiness levels (Phase 7 §16) derived from recorded evidence — never from the fact that a
training script finished. Not a certification or airworthiness statement.

DATA_BLOCKED               the intended classes have no approved, valid training data
EXPERIMENTAL               a real training run completed; required evaluation evidence is missing
EVALUATED_LIMITED          completed run + thresholds chosen on validation + one final held-out test
                           evaluation on the designated manifest, with per-class metrics and documented
                           limitations; the scope is restricted (classes, sources, aircraft)
CANDIDATE_FOR_INTEGRATION  all of the above + per-class targets agreed with a domain reviewer and written
                           down BEFORE the test run (MLG-04) and met, + human review of every missed defect
                           (MLG-06), + a licence-cleared dataset version (MLG-01)
"""
from __future__ import annotations

LEVELS = ("DATA_BLOCKED", "EXPERIMENTAL", "EVALUATED_LIMITED", "CANDIDATE_FOR_INTEGRATION")


def decide(ev: dict) -> dict:
    """ev keys (booleans unless noted): data_ready, run_completed, thresholds_from_validation,
    final_test_evaluation, test_split_digest_matches, per_class_metrics, limitations_documented,
    targets_preregistered (dict class→target or None), targets_met (bool|None), missed_defect_review_done,
    dataset_licence_cleared."""
    reasons = []
    if not ev.get("data_ready"):
        return {"level": "DATA_BLOCKED", "reasons": ["training data for the intended classes is not approved or not valid"]}
    if not ev.get("run_completed"):
        return {"level": None, "reasons": ["no completed training run — a configuration alone is not a model, so no readiness level applies"]}
    need_eval = [k for k in ("thresholds_from_validation", "final_test_evaluation", "test_split_digest_matches", "per_class_metrics", "limitations_documented") if not ev.get(k)]
    if need_eval:
        return {"level": "EXPERIMENTAL", "reasons": [f"missing: {k}" for k in need_eval]}
    level = "EVALUATED_LIMITED"
    if not ev.get("targets_preregistered"):
        reasons.append("MLG-04: no per-class targets were agreed with a domain reviewer before the test run")
    elif not ev.get("targets_met"):
        reasons.append("MLG-04: pre-registered per-class targets were not met")
    if not ev.get("missed_defect_review_done"):
        reasons.append("MLG-06: missed defects have not been reviewed by a qualified person")
    if not ev.get("dataset_licence_cleared"):
        reasons.append("MLG-01: dataset licence/provenance questions are open")
    if not reasons:
        level = "CANDIDATE_FOR_INTEGRATION"
    return {"level": level, "reasons": reasons}
