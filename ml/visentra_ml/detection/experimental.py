"""Development-only experimental detection (Phase 8 preparation).

VT-DET-0001 has **no supported class**. AI_INFERENCE_ARCHITECTURE §6 says detections of classes that
are not supported are never shown to users, and this module does not change that rule for operational
inspections. It adds a separate, isolated mode for developers and administrators:

* **Off by default.** It runs only when ``VISIONTRA_EXPERIMENTAL_DETECTION=1`` **and** the runtime
  environment (``VISENTRA_ENV``) is ``development`` or ``test``. It is refused in production and in any
  unknown environment, whatever the flag says.
* **Allow-listed scope.** Only the model ids and classes in ``ALLOWED`` (VT-DET-0001: ``dent``). The
  model must be below CANDIDATE_FOR_INTEGRATION and its classes must be *experimental*, not supported.
* **Clearly labelled.** Output schema ``visiontra.experimental_detection_output/1`` (different from the
  operational ``visiontra.detection_output/1``), a banner, and ``experimental: true`` on every
  detection. ``operational_use``, ``findings_eligible`` and ``report_eligible`` are always false.
* **Never a finding.** ``assert_operational_eligible`` is the guard every inspection-finding, report or
  prediction-persistence path must call. It rejects experimental output, any model below
  CANDIDATE_FOR_INTEGRATION and any class that is not supported. The only way past it is a new model
  bundle that passes the future readiness gate VT-GATE-OP-01 (``operational_gate``), documented in
  docs/PHASE_8_EXPERIMENTAL_INFERENCE.md.

Nothing here writes to the database or to inspection records.
"""
from __future__ import annotations

import copy
import os

from . import output as O
from .readiness import LEVELS

SCHEMA = "visiontra.experimental_detection_output/1"
FLAG = "VISIONTRA_EXPERIMENTAL_DETECTION"
ALLOWED_ENVS = ("development", "test")
ALLOWED = {"VT-DET-0001": ("dent",)}           # model id → classes that may be shown in experimental mode
BANNER = "EXPERIMENTAL — dent only — development use. Not an inspection finding. Not for maintenance decisions."
GATE_ID = "VT-GATE-OP-01"
OPERATIONAL_MIN_LEVEL = "CANDIDATE_FOR_INTEGRATION"


class ExperimentalModeDisabled(PermissionError):
    code = "EXPERIMENTAL_MODE_DISABLED"


class ExperimentalModelRejected(ValueError):
    code = "EXPERIMENTAL_MODEL_REJECTED"


class NotOperationallyEligible(ValueError):
    """Raised when experimental or not-yet-approved model output reaches an operational path."""
    code = "NOT_OPERATIONALLY_ELIGIBLE"


def _level(meta: dict) -> int:
    r = meta.get("readiness")
    return LEVELS.index(r) if r in LEVELS else -1


def check_enabled(environ: dict | None = None) -> str:
    """Return the environment name, or raise ExperimentalModeDisabled. Default: disabled."""
    env = os.environ if environ is None else environ
    name = (env.get("VISENTRA_ENV") or "development").strip().lower()
    if env.get(FLAG, "").strip() != "1":
        raise ExperimentalModeDisabled(f"experimental detection is off (set {FLAG}=1 in a development or test environment)")
    if name not in ALLOWED_ENVS:
        raise ExperimentalModeDisabled(f"experimental detection is never available in the '{name}' environment")
    return name


def check_model(meta: dict) -> tuple[str, ...]:
    """Return the classes that may be shown in experimental mode, or raise ExperimentalModelRejected."""
    mid = meta.get("model_id")
    if mid not in ALLOWED:
        raise ExperimentalModelRejected(f"{mid} is not allow-listed for experimental inference")
    if _level(meta) >= LEVELS.index(OPERATIONAL_MIN_LEVEL):
        raise ExperimentalModelRejected(f"{mid} is {meta.get('readiness')}: use the operational path, not experimental mode")
    if meta.get("readiness") not in ("EXPERIMENTAL", "EVALUATED_LIMITED"):
        raise ExperimentalModelRejected(f"{mid} readiness {meta.get('readiness')!r} cannot produce output")
    if meta.get("supported_classes"):
        raise ExperimentalModelRejected(f"{mid} declares supported classes; experimental mode only serves experimental-only models")
    allowed = tuple(c for c in ALLOWED[mid] if c in (meta.get("experimental_classes") or []))
    if not allowed or set(meta.get("classes") or []) - set(ALLOWED[mid]):
        raise ExperimentalModelRejected(f"{mid} classes {meta.get('classes')} exceed the allow-listed scope {list(ALLOWED[mid])}")
    return allowed


def label(out: dict, meta: dict) -> dict:
    """Wrap a validated ``visiontra.detection_output/1`` as experimental output (a new dict)."""
    O.validate(out)
    allowed = check_model(meta)
    if any(d["class_name"] not in allowed for d in out["detections"]):
        raise ExperimentalModelRejected("detection of a class outside the experimental scope")
    e = copy.deepcopy(out)
    e["schema"] = SCHEMA
    e["base_schema"] = O.SCHEMA
    e["experimental"] = {
        "mode": "development_only", "banner": BANNER, "scope_classes": list(allowed),
        "readiness": meta.get("readiness"), "operational_use": False, "findings_eligible": False, "report_eligible": False,
        "promotion_gate": GATE_ID, "promotion_gate_passed": False,
    }
    for d in e["detections"]:
        d["experimental"] = True
        d["display_label"] = f"{d['class_name']} (experimental)"
    e["warnings"] = [BANNER] + e["warnings"]
    return e


def is_experimental(o: dict) -> bool:
    return o.get("schema") == SCHEMA or bool(o.get("experimental")) or any(d.get("experimental") for d in o.get("detections", []))


def assert_operational_eligible(o: dict) -> None:
    """Guard for every path that creates inspection findings, reports or persisted predictions."""
    if is_experimental(o):
        raise NotOperationallyEligible("experimental model output can never become an inspection finding or report entry")
    if o.get("schema") != O.SCHEMA:
        raise NotOperationallyEligible(f"unknown output schema {o.get('schema')!r}")
    m = o.get("model") or {}
    if _level(m) < LEVELS.index(OPERATIONAL_MIN_LEVEL):
        raise NotOperationallyEligible(f"model {m.get('model_id')} is {m.get('readiness')}; {GATE_ID} requires {OPERATIONAL_MIN_LEVEL}")
    sup = set(m.get("supported_classes") or [])
    bad = [d["class_name"] for d in o.get("detections", []) if d["class_name"] not in sup]
    if bad:
        raise NotOperationallyEligible(f"classes not supported by the model: {sorted(set(bad))}")


# ------------------------------------------------------------------ future readiness gate VT-GATE-OP-01
GATE_CONDITIONS = (
    ("readiness_candidate", "model bundle readiness is CANDIDATE_FOR_INTEGRATION (readiness.py)"),
    ("class_mapping_accepted", "class mapping ACCEPTED with a named qualified-inspector sign-off (not PROVISIONAL)"),
    ("targets_preregistered_and_met", "per-class targets written down before the test run and met (MLG-04)"),
    ("fresh_test_set", "evaluated once on a test set never used for VT-DET-0001 or any tuning"),
    ("aircraft_independent_evaluation", "evaluation on aircraft/sites not present in training"),
    ("verified_negatives", "false-alarm rate measured on verified defect-free surfaces (MLE-03)"),
    ("missed_defect_review", "every missed defect reviewed by a qualified person (MLG-06)"),
    ("licence_cleared", "dataset licence and provenance cleared for this use (MLG-01)"),
    ("supported_classes_declared", "the bundle declares the classes as supported, under a new model id"),
    ("product_owner_approval", "a signed decision record by the product owner and a qualified inspector"),
)


def operational_gate(meta: dict, evidence: dict | None = None) -> dict:
    """Evaluate VT-GATE-OP-01. ``evidence`` holds booleans keyed by condition (default: none supplied).

    Readiness and supported classes are read from the bundle; every other condition needs an explicit,
    recorded ``True``. The gate is for a *future* model: VT-DET-0001 is immutable and fails it.
    """
    ev = dict(evidence or {})
    ev["readiness_candidate"] = meta.get("readiness") == OPERATIONAL_MIN_LEVEL
    ev["supported_classes_declared"] = bool(meta.get("supported_classes"))
    unmet = [{"condition": k, "requirement": txt} for k, txt in GATE_CONDITIONS if ev.get(k) is not True]
    return {"gate": GATE_ID, "model_id": meta.get("model_id"), "passed": not unmet, "unmet": unmet}
