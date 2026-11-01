"""Phase 8 preparation: development-only experimental inference stays isolated, labelled and out of
operational findings; the held-out test set cannot be scored through it; VT-GATE-OP-01 is enforced.

Torch-free tests run everywhere. Tests needing PyTorch skip without it; the real-bundle fixture test
also skips when the (git-ignored) VT-DET-0001 weights or validation images are absent.
"""
import json
import sys
from pathlib import Path

import pytest

from visentra_ml.detection import config as C
from visentra_ml.detection import experimental as X
from visentra_ml.detection import output as O

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "models/detection/VT-DET-0001"
META = json.loads((BUNDLE / "metadata.json").read_text())
ON = {"VISENTRA_ENV": "development", X.FLAG: "1"}


def _out(meta, score=0.2, cls=0):
    prep = {"mode": "letterbox", "S_w": 416, "S_h": 416, "r_x": 0.65, "r_y": 0.65, "pad_x": 0, "pad_y": 0}
    th = meta["thresholds"]
    return O.build(meta, {"ref": "x", "width": 640, "height": 640}, prep, [{"cls": cls, "score": score, "xyxy": [10, 10, 100, 80]}],
                   score_threshold=th["score_threshold"], review_threshold=th["review_threshold"], nms_iou=th["nms_iou"])


def _candidate(**kw):
    return {**META, "model_id": "VT-DET-0042", "readiness": "CANDIDATE_FOR_INTEGRATION", "supported_classes": ["dent"], "experimental_classes": [], **kw}


# ---------------------------------------------------------------- switch
@pytest.mark.parametrize("env", [{}, {"VISENTRA_ENV": "development"}, {X.FLAG: "0", "VISENTRA_ENV": "development"}, {X.FLAG: "true"},
                                 {X.FLAG: "1", "VISENTRA_ENV": "production"}, {X.FLAG: "1", "VISENTRA_ENV": "PRODUCTION"},
                                 {X.FLAG: "1", "VISENTRA_ENV": "staging"}])
def test_mode_is_off_by_default_and_never_in_production(env):
    with pytest.raises(X.ExperimentalModeDisabled):
        X.check_enabled(env)


def test_mode_on_only_with_flag_in_development_or_test():
    assert X.check_enabled(ON) == "development"
    assert X.check_enabled({**ON, "VISENTRA_ENV": "test"}) == "test"


# ---------------------------------------------------------------- model scope
def test_real_bundle_is_dent_only_experimental():
    assert META["model_id"] == "VT-DET-0001" and META["readiness"] == "EVALUATED_LIMITED"
    assert META["supported_classes"] == [] and META["experimental_classes"] == ["dent"]
    assert X.check_model(META) == ("dent",)


@pytest.mark.parametrize("change", [{"model_id": "VT-DET-0999"}, {"readiness": "CANDIDATE_FOR_INTEGRATION"}, {"readiness": "DATA_BLOCKED"},
                                    {"readiness": None}, {"supported_classes": ["dent"]}, {"classes": ["dent", "crack"]},
                                    {"experimental_classes": []}])
def test_models_outside_the_allow_listed_scope_are_rejected(change):
    with pytest.raises(X.ExperimentalModelRejected):
        X.check_model({**META, **change})


# ---------------------------------------------------------------- labelling
def test_experimental_output_is_clearly_labelled_and_not_eligible():
    e = X.label(_out(META), META)
    assert e["schema"] == X.SCHEMA and e["base_schema"] == O.SCHEMA
    x = e["experimental"]
    assert x["operational_use"] is False and x["findings_eligible"] is False and x["report_eligible"] is False
    assert x["promotion_gate"] == "VT-GATE-OP-01" and x["promotion_gate_passed"] is False and x["scope_classes"] == ["dent"]
    assert e["warnings"][0] == X.BANNER and "dent only" in X.BANNER and O.SUPPORT_WARNING in e["warnings"]
    assert all(d["experimental"] and d["display_label"] == "dent (experimental)" for d in e["detections"])
    with pytest.raises(O.InvalidModelOutput):          # the operational validator never accepts it
        O.validate(e)


def test_no_detection_output_keeps_its_warning_when_labelled():
    e = X.label(_out(META, score=0.01), META)
    assert e["status"] == "no_detections" and O.NO_DETECTION_WARNING in e["warnings"]


# ---------------------------------------------------------------- operational guard
def test_experimental_output_can_never_become_a_finding():
    e = X.label(_out(META), META)
    with pytest.raises(X.NotOperationallyEligible):
        X.assert_operational_eligible(e)
    forged = json.loads(json.dumps(e)); forged["schema"] = O.SCHEMA; forged.pop("experimental")
    with pytest.raises(X.NotOperationallyEligible):          # a per-detection marker is enough
        X.assert_operational_eligible(forged)


def test_unlabelled_output_of_a_limited_model_is_still_rejected():
    with pytest.raises(X.NotOperationallyEligible, match="EVALUATED_LIMITED"):
        X.assert_operational_eligible(_out(META))


def test_only_a_candidate_model_with_supported_classes_passes_the_guard():
    X.assert_operational_eligible(_out(_candidate()))
    unsupported = _candidate(classes=["dent", "crack"], supported_classes=["dent"], experimental_classes=["crack"],
                             thresholds={**META["thresholds"], "score_threshold": {"dent": 0.05, "crack": 0.05}, "review_threshold": {"dent": 0.075, "crack": 0.075}})
    with pytest.raises(X.NotOperationallyEligible, match="crack"):
        X.assert_operational_eligible(_out(unsupported, cls=1))


# ---------------------------------------------------------------- VT-GATE-OP-01
def test_vt_det_0001_fails_the_operational_gate():
    g = X.operational_gate(META)
    assert g["gate"] == "VT-GATE-OP-01" and not g["passed"] and len(g["unmet"]) == len(X.GATE_CONDITIONS)


def test_gate_reads_readiness_from_the_bundle_not_from_claimed_evidence():
    everything = {k: True for k, _ in X.GATE_CONDITIONS}
    g = X.operational_gate(META, everything)
    assert not g["passed"] and {u["condition"] for u in g["unmet"]} == {"readiness_candidate", "supported_classes_declared"}
    assert X.operational_gate(_candidate(), everything)["passed"]
    partial = {**everything, "verified_negatives": None}
    assert [u["condition"] for u in X.operational_gate(_candidate(), partial)["unmet"]] == ["verified_negatives"]


# ---------------------------------------------------------------- CLI guards (no torch needed to refuse)
@pytest.fixture
def cli(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import experimental_inference as EI
    monkeypatch.setattr(C, "ROOT", tmp_path)
    img = tmp_path / "img.png"; img.write_bytes(b"\x89PNG fake bytes for hashing only")
    (tmp_path / "datasets/splits/VT-PREP-999").mkdir(parents=True)
    (tmp_path / "models/detection/VT-DET-0001").mkdir(parents=True)
    return EI, tmp_path, img


def test_cli_refuses_when_disabled(cli, monkeypatch, capsys):
    EI, root, img = cli
    monkeypatch.delenv(X.FLAG, raising=False)
    assert EI.main(["models/detection/VT-DET-0001", str(img)]) == 3
    assert not (root / "experiments").exists()


def test_cli_refuses_held_out_test_images(cli, monkeypatch, capsys):
    EI, root, img = cli
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    (root / "datasets/splits/VT-PREP-999/test.csv").write_text(f"file_id,sha256\nX:1,{EI.sha256(img)}\n")
    assert EI.main(["models/detection/VT-DET-0001", str(img)]) == 5
    assert "held-out test image" in capsys.readouterr().err and not (root / "experiments").exists()


@pytest.mark.parametrize("out", ["datasets/x", "data/x", "experiments/dev-inference", "experiments/other"])
def test_cli_writes_only_inside_dev_inference(cli, monkeypatch, out):
    EI, root, img = cli
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    assert EI.main(["models/detection/VT-DET-0001", str(img), "--out", out]) == 2
    assert not (root / out).exists()


# ---------------------------------------------------------------- with PyTorch
def test_predictor_matches_the_prediction_cli_and_cli_labels_output(tmp_path, monkeypatch, capsys):
    torch = pytest.importorskip("torch")
    from PIL import Image
    from visentra_ml.detection import artifact as A, model as Mo
    from visentra_ml.detection.inference import Predictor
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import experimental_inference as EI
    import predict_image as P
    monkeypatch.setattr(C, "ROOT", tmp_path)
    meta = {"version": "0.0.1", "task": "detection", "architecture": "YOLOX", "variant": "nano", "framework": {}, "classes": ["dent"],
            "supported_classes": [], "experimental_classes": ["dent"], "input_size": [64, 64], "preprocessing": {"fill": 114}, "taxonomy": "TAX-1.0",
            "class_mapping": {}, "dataset": {}, "experiment": {}, "weights_licence": {}, "pretrained": None, "readiness": "EVALUATED_LIMITED",
            "thresholds": {"score_threshold": {"dent": 0.0}, "review_threshold": {"dent": 0.5}, "nms_iou": 0.65}, "known_limitations": ["synthetic"]}
    torch.manual_seed(0)
    A.package("VT-DET-9300", Mo.build("nano", 1).state_dict(), meta, out_root=tmp_path / "models")
    img = tmp_path / "s.png"; Image.new("RGB", (120, 80), (120, 120, 125)).save(img)
    assert P.main(["models/VT-DET-9300", str(img), "--ref", "s"]) == 0
    cli_out = json.loads(capsys.readouterr().out)
    p = Predictor(tmp_path / "models/VT-DET-9300")
    direct = p.predict_file(img, "s")
    strip = lambda o: {k: v for k, v in o.items() if k != "inferred_at"}
    assert strip(direct) == strip(cli_out) and strip(p.predict_file(img, "s")) == strip(direct)   # repeatable
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    assert EI.main(["models/VT-DET-9300", str(img)]) == 4                    # not allow-listed
    monkeypatch.setitem(X.ALLOWED, "VT-DET-9300", ("dent",))
    assert EI.main(["models/VT-DET-9300", str(img), "--out", "experiments/dev-inference/t"]) == 0
    o = json.loads((tmp_path / "experiments/dev-inference/t/dev-0001.json").read_text())
    run = json.loads((tmp_path / "experiments/dev-inference/t/run.json").read_text())
    assert o["schema"] == X.SCHEMA and o["experimental"]["findings_eligible"] is False and o["input"]["sha256"] == EI.sha256(img)
    assert run["metrics_computed"] is False and run["operational_use"] is False
    with pytest.raises(X.NotOperationallyEligible):
        X.assert_operational_eligible(o)


def test_real_bundle_integration_fixture_is_deterministic():
    pytest.importorskip("torch")
    if not (BUNDLE / "model.pt").is_file():
        pytest.skip("VT-DET-0001 weights are git-ignored and not present here")
    spec = json.loads((REPO / "ml/fixtures/VT-DET-0001.inference_fixture.json").read_text())
    if not all((REPO / v["rel_path"]).is_file() for v in spec["validation"]):
        pytest.skip("validation images not present")
    test_csv = (REPO / "datasets/splits/VT-PREP-001/test.csv").read_text()
    assert not any(v["sha256"] in test_csv for v in spec["validation"]) and spec["metrics_computed"] is False
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import inference_fixture as F
    assert F.main(["check", "models/detection/VT-DET-0001"]) == 0
