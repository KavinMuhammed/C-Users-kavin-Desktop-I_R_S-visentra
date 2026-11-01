"""Phase 7 detection pipeline — parts that need PyTorch: augmentation geometry, training records,
reproducibility, interruption/resume, error reporting, checkpoint and artefact loading.

Uses the synthetic miniature project from test_detection_core (generated flat-colour images).
"""
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from visentra_ml.detection import artifact as A  # noqa: E402
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as D  # noqa: E402
from visentra_ml.detection import engine as E  # noqa: E402
from visentra_ml.detection import model as Mo  # noqa: E402

from test_detection_core import make_project  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    return tmp_path, make_project(tmp_path)


def test_augmentation_moves_boxes_with_the_image(project):
    root, cfg = project
    items, _ = D.load_items(cfg, "train", D.load_mapping(cfg))
    it = next(i for i in items if i.boxes)
    plain = D.DetectionDataset([it], (64, 64))
    _, t0, meta = plain[0]
    # 64×48 image letterboxed into 64×64: ratio 1, vertical padding 8 px; dent box 0.375–0.625 of width
    assert meta["ratio"] == 1 and meta["pad_y"] == 8
    assert t0[0].tolist() == pytest.approx([0, 32, 32, 16, 12])
    flip = D.DetectionDataset([it], (64, 64), augment={"scale": [1, 1], "translate": 0, "hflip": 1.0, "vflip": 0, "hsv": {}})
    img, t1, _ = flip[0]
    assert t1[0].tolist() == pytest.approx([0, 64 - 32, 32, 16, 12])
    det = torch.tensor([[t0[0, 1] - 8, t0[0, 2] - 6, t0[0, 1] + 8, t0[0, 2] + 6, 0.9, 0]])
    back = Mo.to_original(det, meta)[0]["xyxy"]
    assert back == pytest.approx([it.boxes[0].x1 * 64, it.boxes[0].y1 * 48, it.boxes[0].x2 * 64, it.boxes[0].y2 * 48])


def test_postprocess_threshold_nms_and_order():
    raw = torch.zeros(1, 4, 6)
    raw[0, :, :4] = torch.tensor([[20, 20, 10, 10], [21, 20, 10, 10], [60, 60, 8, 8], [5, 5, 4, 4]], dtype=torch.float)
    raw[0, :, 4] = torch.tensor([0.9, 0.8, 0.7, 0.01]); raw[0, :, 5] = 1.0
    out = Mo.postprocess(raw, 1, score_floor=0.05, nms_iou=0.5, max_det=10)[0]
    assert out.shape[0] == 2 and out[0, 4] == pytest.approx(0.9) and out[1, 4] == pytest.approx(0.7)


def test_pretrained_loading_checks_checksum_and_skips_class_head(project, tmp_path):
    root, cfg = project
    src = Mo.build("nano", 80)
    p = root / "models/pretrained/fake.pth"; p.parent.mkdir(parents=True)
    torch.save({"model": src.state_dict()}, p)
    m = Mo.build("nano", 1)
    info = Mo.load_pretrained(m, {"path": "models/pretrained/fake.pth", "sha256": Mo.sha256_file(p)})
    assert info["skipped_shape_mismatch"] and all("cls_preds" in k for k in info["skipped_shape_mismatch"])
    assert torch.equal(m.backbone.backbone.stem.conv.conv.weight, src.backbone.backbone.stem.conv.conv.weight)
    with pytest.raises(ValueError, match="checksum"):
        Mo.load_pretrained(m, {"path": "models/pretrained/fake.pth", "sha256": "0" * 64})


def _history(run_dir):
    return [json.loads(l) for l in (run_dir / "history.jsonl").read_text().splitlines()]


def test_training_writes_complete_records_and_is_reproducible(project):
    root, cfg = project
    r1 = E.train(cfg, run_dir=root / "experiments/VT-EXP-901/run-a", echo=lambda *_: None)
    r2 = E.train(cfg, run_dir=root / "experiments/VT-EXP-901/run-b", echo=lambda *_: None)
    d = root / "experiments/VT-EXP-901/run-a"
    for f in ("run.json", "config.json", "environment.json", "history.jsonl", "train.log", "checkpoints/last.pt", "checkpoints/best.pt"):
        assert (d / f).is_file(), f
    rec = json.loads((d / "run.json").read_text())
    assert rec["status"] == "completed" and rec["epochs_completed"] == 2 and rec["config_sha256"] == C.digest(cfg)
    assert rec["data"]["train"]["instances"] == {"dent": 4} and rec["data"]["train"]["dropped_source_instances"] == {"line": 4}
    h1, h2 = _history(d), _history(root / "experiments/VT-EXP-901/run-b")
    assert [round(h["total_loss"], 4) for h in h1] == [round(h["total_loss"], 4) for h in h2], "same seed must reproduce the losses"
    assert "valid_AP50" in h1[-1] and r1["status"] == r2["status"] == "completed"
    with pytest.raises(FileExistsError):                      # an earlier run is never overwritten
        E.train(cfg, run_dir=d, echo=lambda *_: None)


def test_interrupted_training_resumes(project, monkeypatch):
    root, cfg = project
    calls = {"n": 0}
    orig = D.collate

    def collate_then_sigterm(batch, *a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            signal.raise_signal(signal.SIGTERM)
        return orig(batch, *a, **k)
    monkeypatch.setattr(D, "collate", collate_then_sigterm)
    d = root / "experiments/VT-EXP-901/run-int"
    rec = E.train(cfg, run_dir=d, echo=lambda *_: None)
    assert rec["status"] == "interrupted" and (d / "checkpoints/last.pt").is_file()
    assert json.loads((d / "run.json").read_text())["events"][-1]["event"] == "interrupted"
    monkeypatch.setattr(D, "collate", orig)
    rec = E.train(cfg, run_dir=d, resume=True, echo=lambda *_: None)
    assert rec["status"] == "completed" and rec["epochs_completed"] == 2
    assert [e["event"] for e in rec["events"]][:4] == ["started", "interrupted", "resumed", "completed"][:4]


def test_training_failure_is_recorded(project, monkeypatch):
    root, cfg = project
    real_forward = Mo.YOLOX.forward

    def nan_forward(self, x, targets=None):
        out = real_forward(self, x, targets)
        if targets is not None:
            out["total_loss"] = out["total_loss"] * float("nan")
        return out
    monkeypatch.setattr(Mo.YOLOX, "forward", nan_forward)
    d = root / "experiments/VT-EXP-901/run-nan"
    with pytest.raises(FloatingPointError):
        E.train(cfg, run_dir=d, echo=lambda *_: None)
    rec = json.loads((d / "run.json").read_text())
    assert rec["status"] == "failed" and rec["error"]["type"] == "FloatingPointError" and "non-finite loss" in rec["error"]["message"]


def _meta(classes=("dent",), readiness="EXPERIMENTAL"):
    return {"version": "0.0.1", "task": "detection", "architecture": "YOLOX", "variant": "nano", "framework": {}, "classes": list(classes),
            "supported_classes": [], "experimental_classes": list(classes), "input_size": [64, 64], "preprocessing": {}, "taxonomy": "TAX-1.0",
            "class_mapping": {}, "dataset": {}, "experiment": {}, "thresholds": {"score_threshold": {c: 0.3 for c in classes}, "review_threshold": None},
            "weights_licence": {}, "pretrained": None, "readiness": readiness, "known_limitations": ["synthetic test bundle"]}


def test_artifact_package_verify_load_and_tamper(tmp_path):
    m = Mo.build("nano", 1)
    d = A.package("VT-DET-9001", m.state_dict(), _meta(), out_root=tmp_path)
    v = A.verify(d)
    assert v["ok"], v["problems"]
    m2, meta = A.load(d)
    assert meta["model_id"] == "VT-DET-9001" and all(torch.equal(a, b) for a, b in zip(m.state_dict().values(), m2.state_dict().values()))
    with pytest.raises(A.ArtifactError, match="immutable"):
        A.package("VT-DET-9001", m.state_dict(), _meta(), out_root=tmp_path)
    raw = bytearray((d / "model.pt").read_bytes()); raw[-10] ^= 0xFF; (d / "model.pt").write_bytes(bytes(raw))
    assert not A.verify(d)["ok"]
    with pytest.raises(A.ArtifactError):
        A.load(d)


def test_artifact_metadata_consistency_rules():
    base = {**_meta(), "model_id": "VT-DET-0001"}
    assert not A.check_meta(base)
    assert any("supported classes" in p for p in A.check_meta({**base, "supported_classes": ["dent"], "experimental_classes": []}))
    assert any("supported or experimental" in p for p in A.check_meta({**base, "experimental_classes": []}))
    assert any("one value per class" in p for p in A.check_meta({**base, "thresholds": {"score_threshold": {"crack": 0.3}}}))
    assert any("model_id" in p for p in A.check_meta({**base, "model_id": "x"}))
    with pytest.raises(A.ArtifactError):
        A.package("VT-DET-9002", {}, {**_meta(), "readiness": "SHIPPED"}, out_root=Path("/tmp/never-written"))


def test_bundle_loads_in_a_fresh_process(tmp_path):
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import inspect_model_artifact as I
    d = A.package("VT-DET-9003", Mo.build("nano", 1).state_dict(), _meta(), out_root=tmp_path)
    r = subprocess.run([sys.executable, "-c", I.CHILD, str(d), str(REPO / "ml")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["loaded"] and out["identical_runs"] and out["fingerprint_1"] == out["fingerprint_2"] and out["model_id"] == "VT-DET-9003"


def test_end_to_end_valid_thresholds_single_test_evaluation_and_packaging(project, capsys):
    root, cfg = project
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import evaluate_detector as EV
    import export_model_metadata as EX
    d = root / "experiments/VT-EXP-901/run-e2e"
    E.train(cfg, run_dir=d, echo=lambda *_: None)
    rel = str(d.relative_to(root))
    assert EV.main(["--run", rel, "--split", "test"]) == 2                     # test needs --final
    assert EV.main(["--run", rel, "--split", "test", "--final"]) == 2          # and thresholds chosen on validation first
    assert EV.main(["--run", rel, "--split", "valid"]) == 0
    th = json.loads((d / "thresholds.json").read_text())
    assert th["selected_on"] == "valid" and "dent" in th["classes"]
    assert EV.main(["--run", rel, "--split", "test", "--final", "--robustness"]) == 0
    assert EV.main(["--run", rel, "--split", "test", "--final"]) == 2          # a second test evaluation is refused
    res = json.loads(next((d / "evaluations").glob("test-*")).joinpath("metrics.json").read_text())
    assert res["split"] == "test" and res["thresholds_from"].startswith("valid-") and set(res["robustness"]) == {"darker_40pct", "brighter_40pct", "low_contrast_50pct", "half_resolution"}
    assert res["metrics"]["per_class_ap"]["dent"]["gt_instances"] == 2 and "recall_by_size" in res["error_analysis"]
    assert EX.main(["--run", rel, "--model-id", "VT-DET-9100"]) == 0
    b = root / "models/detection/VT-DET-9100"
    meta = json.loads((b / "metadata.json").read_text())
    assert A.verify(b)["ok"] and meta["readiness"] == "EVALUATED_LIMITED" and meta["supported_classes"] == [] and meta["experimental_classes"] == ["dent"]
    assert meta["dataset"]["test_split_digest"] == res["split_digest"] and (b / "evaluation_test.json").is_file()
    smoke = root / "experiments/VT-EXP-901/run-smoke"
    E.train(cfg, run_dir=smoke, smoke={"train": 3, "valid": 2, "epochs": 1}, echo=lambda *_: None)
    assert EV.main(["--run", str(smoke.relative_to(root)), "--split", "valid"]) == 2   # smoke runs are never evaluated as models


def test_predict_image_cli_returns_a_valid_output_contract(tmp_path, monkeypatch, capsys):
    from PIL import Image
    from visentra_ml.detection import output as O
    sys.path.insert(0, str(REPO / "scripts/ml"))
    import predict_image as P
    monkeypatch.setattr(C, "ROOT", tmp_path)
    meta = _meta(); meta["thresholds"] = {"score_threshold": {"dent": 0.3}, "review_threshold": {"dent": 0.5}, "nms_iou": 0.65}
    meta["preprocessing"] = {"fill": 114}
    A.package("VT-DET-9200", Mo.build("nano", 1).state_dict(), meta, out_root=tmp_path / "models")
    img = tmp_path / "synthetic.jpg"; Image.new("RGB", (120, 80), (128, 128, 128)).save(img)
    assert P.main(["models/VT-DET-9200", str(img), "--ref", "synthetic-1"]) == 0
    out = json.loads(capsys.readouterr().out)
    O.validate(out)
    assert out["input"]["width"] == 120 and out["input"]["height"] == 80 and out["model"]["model_id"] == "VT-DET-9200"
    assert out["input"]["preprocessing"]["pad_y"] == 10   # (64 − 43) / 2 = 10.5 → round-half-even 10, same as training and out["model"]["experimental_classes"] == ["dent"]
