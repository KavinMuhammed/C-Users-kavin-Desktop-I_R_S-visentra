"""Phase 7 detection pipeline — pure-Python parts (no PyTorch needed).

Synthetic fixtures only: tiny generated label files and boxes in pytest's tmp_path. Nothing here is
an aircraft image, and nothing is written to datasets/.
"""
import copy
import csv
import json
import math
from pathlib import Path

import pytest

from visentra_ml.detection import config as C
from visentra_ml.detection import data as D
from visentra_ml.detection import geometry as G
from visentra_ml.detection import metrics as M
from visentra_ml.detection import output as O
from visentra_ml.detection import readiness as R

REPO = Path(__file__).resolve().parents[1]
REAL_CFG = REPO / "ml/configs/detection/VT-EXP-001.json"


# ------------------------------------------------------------------ synthetic project fixture
def make_project(root: Path, *, crossing=False, missing_image=False, bad_label=False, mapping_status="PROVISIONAL_ACCEPTED"):
    """A miniature repository layout: 2 source classes (0 'bump' → dent, 1 'line' → crack UNRESOLVED)."""
    from PIL import Image
    raw = root / "datasets/raw/VT-DS-901"
    rows = {"train": [], "valid": [], "test": []}
    k = 0
    for split, n in (("train", 6), ("valid", 3), ("test", 3)):
        (raw / split / "images").mkdir(parents=True, exist_ok=True); (raw / split / "labels").mkdir(parents=True, exist_ok=True)
        for i in range(n):
            k += 1
            name = f"syn_{k:03d}"
            img = raw / split / "images" / f"{name}.jpg"
            if not (missing_image and split == "valid" and i == 0):
                Image.new("RGB", (64, 48), (90 + 10 * i, 120, 140)).save(img)
            lab = "0 0.5 0.5 0.25 0.25\n1 0.2 0.2 0.1 0.1\n" if i % 3 != 2 else ""
            if bad_label and split == "train" and i == 0:
                lab = "0 1.4 0.5 0.25 0.25\n"
            (raw / split / "labels" / f"{name}.txt").write_text(lab)
            pg = f"PG-{k:03d}" if not (crossing and split == "test" and i == 0) else "PG-001"
            rows[split].append({"file_id": f"VT-DS-901:{split}/images/{name}.jpg", "dataset_id": "VT-DS-901", "rel_path": f"datasets/raw/VT-DS-901/{split}/images/{name}.jpg",
                                "sha256": "", "photo_group": pg, "split_group": pg, "source_split": split, "annotation_status": "VALID", "source_classes": "",
                                "mapped_classes": "", "unresolved_candidate_classes": "", "flags": "", "eval_representative": "yes" if split != "train" else "n/a (train)", "evaluation_scope": ""})
    sp = root / "datasets/splits/VT-PREP-901"; sp.mkdir(parents=True)
    for s, rs in rows.items():
        with open(sp / f"{s}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rs[0])); w.writeheader(); w.writerows(rs)
    (root / "datasets/config").mkdir(parents=True)
    (root / "datasets/config/class_mapping.json").write_text(json.dumps({
        "mapping_id": "CMAP-T", "version": 1, "taxonomy": "TAX-1.0", "target_classes": [{"id": 0, "name": "crack"}, {"id": 1, "name": "dent"}, {"id": 2, "name": "corrosion"}],
        "entries": [{"dataset": "VT-DS-901", "source_class_id": 0, "source_name": "bump", "target": "dent", "status": mapping_status},
                    {"dataset": "VT-DS-901", "source_class_id": 1, "source_name": "line", "target": "crack", "status": "UNRESOLVED"}]}))
    cfg = json.loads(REAL_CFG.read_text())
    cfg["experiment_id"] = "VT-EXP-901"
    cfg["dataset"].update({"prep_id": "VT-PREP-901", "datasets": ["VT-DS-901"], "splits": {s: f"datasets/splits/VT-PREP-901/{s}.csv" for s in rows},
                           "valid_subset": "all", "test_subset": "eval_representatives"})
    cfg["model"].update({"variant": "nano", "input_size": [64, 64]}); cfg["model"].pop("pretrained")
    cfg["train"].update({"epochs": 2, "batch_size": 3, "num_threads": 1, "early_stopping": {"metric": "AP50_95", "patience": 5}})
    cfg["output_dir"] = "experiments/VT-EXP-901"
    return cfg


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    return tmp_path, make_project(tmp_path)


# ------------------------------------------------------------------ configuration
def test_real_experiment_config_is_valid_and_dent_only():
    cfg = C.load(REAL_CFG)
    assert cfg["dataset"]["target_classes"] == ["dent"] and cfg["dataset"]["datasets"] == ["VT-DS-001"]
    assert cfg["train"]["selection_split"] == "valid"
    assert cfg["model"]["pretrained"]["licence"].startswith("Apache-2.0")


@pytest.mark.parametrize("mutate, expect", [
    (lambda c: c.update(schema="x"), "schema"),
    (lambda c: c.update(experiment_id="EXP1"), "experiment_id"),
    (lambda c: c["dataset"].update(mapping_statuses_allowed=["UNRESOLVED"]), "mapping_statuses_allowed"),
    (lambda c: c["train"].update(selection_split="test"), "selection_split"),
    (lambda c: c["train"].update(epochs=0), "train.epochs"),
    (lambda c: c["train"].update(batch_size=2.5), "train.batch_size"),
    (lambda c: c["augment"].update(mosaic=0.5), "mosaic"),
    (lambda c: c["augment"].update(vflip=0.5), "vflip_justification"),
    (lambda c: c["augment"]["hsv"].update(h=0.3), "augment.hsv.h"),
    (lambda c: c["model"].update(input_size=[400, 400]), "input_size"),
    (lambda c: c["model"].update(variant="huge"), "variant"),
    (lambda c: c.update(output_dir="datasets/raw/out"), "datasets/"),
    (lambda c: c.update(output_dir="../outside"), "escapes"),
    (lambda c: c["dataset"]["splits"].update(train="/etc/passwd"), "repository-relative"),
    (lambda c: c["dataset"]["splits"].update(train="datasets/splits/none.csv"), "does not exist"),
    (lambda c: c["model"]["pretrained"].update(sha256="abc"), "pretrained"),
])
def test_invalid_configurations_are_rejected(mutate, expect):
    cfg = json.loads(REAL_CFG.read_text())
    mutate(cfg)
    problems = C.validate(cfg)
    assert any(expect in p for p in problems), problems


def test_config_digest_is_stable_and_order_independent():
    a = json.loads(REAL_CFG.read_text()); b = json.loads(json.dumps(a, sort_keys=True))
    assert C.digest(a) == C.digest(b)
    b["train"]["seed"] += 1
    assert C.digest(a) != C.digest(b)


# ------------------------------------------------------------------ class mapping and labels
def test_mapping_uses_only_approved_entries(project):
    root, cfg = project
    m = D.load_mapping(cfg)
    assert m["use"] == {("VT-DS-901", 0): 0}
    assert m["dropped"][("VT-DS-901", 1)]["status"] == "UNRESOLVED"


def test_unresolved_target_cannot_be_trained(project):
    root, cfg = project
    cfg["dataset"]["target_classes"] = ["dent", "crack"]
    with pytest.raises(D.DataError, match="crack"):
        D.load_mapping(cfg)


def test_mapping_with_unresolved_status_only_blocks_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    cfg = make_project(tmp_path, mapping_status="UNRESOLVED")
    with pytest.raises(D.DataError):
        D.load_mapping(cfg)


def test_real_mapping_dent_only_and_unresolved_dropped():
    cfg = C.load(REAL_CFG)
    m = D.load_mapping(cfg)
    assert m["use"] == {("VT-DS-001", 1): 0}
    names = {d["source_name"]: d["status"] for d in m["dropped"].values()}
    # CMAP-001 v2 (2026-10-17): crack is PROVISIONAL_ACCEPTED but not a target of this dent-only config, so it is still dropped here
    assert names["crack"] == "PROVISIONAL_ACCEPTED" and names["scratch"] == "NOT_MAPPED" and names["paint-off"] == "NOT_MAPPED" and names["missing-head"] == "NOT_MAPPED"


def test_parse_yolo_boxes_polygons_and_problems():
    mp = {"use": {("X", 0): 0}, "dropped": {("X", 1): {"source_name": "line", "status": "UNRESOLVED"}}}
    b, dropped, pr = D.parse_yolo("0 0.5 0.5 0.2 0.4\n0 0.1 0.1 0.3 0.1 0.3 0.4\n1 0.5 0.5 0.1 0.1\n", "X", mp)
    assert len(b) == 2 and not pr and dropped["line"] == 1
    assert (b[0].x1, b[0].y1, b[0].x2, b[0].y2) == pytest.approx((0.4, 0.3, 0.6, 0.7))
    assert (b[1].x1, b[1].y1, b[1].x2, b[1].y2) == pytest.approx((0.1, 0.1, 0.3, 0.4))      # polygon → bounding box
    for bad, why in [("0 1.3 0.5 0.2 0.2", "outside"), ("0 nan 0.5 0.2 0.2", "non-finite"), ("0 0.5 0.5 0.2", "field count"),
                     ("0 0.5 0.5 0 0.2", "degenerate"), ("a b c d e", "not numeric"), ("7 0.5 0.5 0.1 0.1", "not in the class mapping")]:
        _, _, pr = D.parse_yolo(bad, "X", mp)
        assert pr and why in pr[0], (bad, pr)


def test_missing_image_and_label_handling(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    cfg = make_project(tmp_path, missing_image=True)
    m = D.load_mapping(cfg)
    with pytest.raises(D.DataError, match="image missing"):
        D.load_items(cfg, "valid", m)
    items, problems = D.load_items(cfg, "valid", m, strict=False)
    assert len(items) == 2 and any("image missing" in p for p in problems)
    (tmp_path / "datasets/raw/VT-DS-901/test/labels/syn_010.txt").unlink()
    _, problems = D.load_items(cfg, "test", m, strict=False)
    assert any("label file missing" in p for p in problems)


def test_empty_label_file_is_unlabelled_not_negative(project):
    root, cfg = project
    items, _ = D.load_items(cfg, "train", D.load_mapping(cfg))
    empty = [i for i in items if i.unlabelled]
    assert empty and all(not i.boxes for i in empty)


def test_readiness_gate_reports_each_condition(project):
    root, cfg = project
    rep = D.readiness(cfg)
    assert rep["ready"] is True
    for k in ("class_mapping_approved", "files_present", "files_readable", "annotations_valid", "no_file_or_group_crosses_splits", "each_target_class_present_in_every_split"):
        assert rep["conditions"][k]["ok"], k


def test_readiness_gate_detects_leakage_and_bad_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    cfg = make_project(tmp_path, crossing=True, bad_label=True)
    rep = D.readiness(cfg)
    assert not rep["ready"]
    assert not rep["conditions"]["no_file_or_group_crosses_splits"]["ok"]
    assert not rep["conditions"]["annotations_valid"]["ok"]


def test_epoch_sampling_one_file_per_asset():
    import random
    items = [D.Item(f"f{i}", "X", "", "", "", f"a{i // 3}", "", "") for i in range(9)]
    idx = D.epoch_indices(items, "one_file_per_asset_per_epoch", random.Random(1))
    assert len(idx) == 3 and len({items[i].asset_id for i in idx}) == 3
    assert sorted(D.epoch_indices(items, "all_files", random.Random(1))) == list(range(9))


def test_split_digest_is_order_independent():
    a = [D.Item(f"f{i}", "X", "", "", "", "", "", "") for i in range(5)]
    assert D.split_digest(a) == D.split_digest(list(reversed(a)))


# ------------------------------------------------------------------ metrics
def test_iou_and_box_validation():
    assert M.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert M.iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(50 / 150)
    assert M.iou([0, 0, 1, 1], [2, 2, 3, 3]) == 0.0
    assert M.validate_box([1, 2, 3, 4], 10, 10) and not M.validate_box([3, 2, 1, 4]) and not M.validate_box([0, 0, math.inf, 1])
    assert not M.validate_box([0, 0, 11, 5], 10, 10)


def test_matching_counts_tp_fp_fn():
    gts = {"a": [(0, [0, 0, 10, 10]), (0, [20, 20, 30, 30])], "b": [(0, [0, 0, 10, 10])], "c": []}
    preds = {"a": [(0, 0.9, [0, 0, 10, 10]), (0, 0.8, [1, 1, 10, 10]), (0, 0.7, [50, 50, 60, 60])],   # TP, duplicate → FP, FP
             "b": [(0, 0.2, [0, 0, 10, 10])], "c": [(0, 0.95, [0, 0, 5, 5])]}
    m = M.match(gts, preds, iou_thr=0.5)
    assert m["per_class"][0] == {"tp": 2, "fp": 3, "fn": 1}
    m = M.match(gts, preds, iou_thr=0.5, score_thr=0.5)       # b's prediction drops below the threshold → FN
    assert m["per_class"][0] == {"tp": 1, "fp": 3, "fn": 2}
    assert M.match({"a": [(0, [0, 0, 10, 10])]}, {"a": [(1, 0.9, [0, 0, 10, 10])]})["per_class"] == {0: {"tp": 0, "fp": 0, "fn": 1}, 1: {"tp": 0, "fp": 1, "fn": 0}}


def test_prf_keeps_undefined_values_undefined():
    assert M.prf(0, 0, 0) == {"tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None, "f1": None}
    assert M.prf(0, 0, 3)["precision"] is None and M.prf(0, 0, 3)["recall"] == 0 and M.prf(0, 0, 3)["f1"] == 0.0
    assert M.prf(0, 4, 0)["recall"] is None and M.prf(0, 4, 0)["f1"] is None
    assert M.prf(3, 1, 1)["f1"] == pytest.approx(0.75)


def test_coco_ap_reference_values():
    pytest.importorskip("pycocotools")
    sizes = {"a": (100, 100), "b": (100, 100)}
    gts = {"a": [(0, [10, 10, 40, 40])], "b": [(0, [50, 50, 90, 90])]}
    perfect = {"a": [(0, 0.9, [10, 10, 40, 40])], "b": [(0, 0.8, [50, 50, 90, 90])]}
    ap = M.coco_ap(gts, perfect, ["dent", "crack"], sizes)
    assert ap["dent"]["AP50"] == pytest.approx(1.0) and ap["dent"]["AP50_95"] == pytest.approx(1.0)
    assert ap["crack"]["AP50"] is None and ap["crack"]["gt_instances"] == 0            # undefined, not 0 or 1
    assert M.coco_ap(gts, {}, ["dent"], sizes)["dent"]["AP50"] == 0.0
    half = {"a": [(0, 0.9, [10, 10, 40, 40])], "b": [(0, 0.8, [0, 0, 5, 5])]}           # one hit, one miss
    assert M.coco_ap(gts, half, ["dent"], sizes)["dent"]["AP50"] == pytest.approx(0.5, abs=0.02)


def test_best_f1_threshold_selection():
    gts = {"a": [(0, [0, 0, 10, 10])], "b": [(0, [0, 0, 10, 10])]}
    preds = {"a": [(0, 0.9, [0, 0, 10, 10]), (0, 0.3, [50, 50, 60, 60])], "b": [(0, 0.6, [0, 0, 10, 10])]}
    r = M.best_f1_threshold(gts, preds, 0, iou_thr=0.5)
    assert r["best"]["f1"] == 1.0 and 0.3 < r["best"]["threshold"] <= 0.6


# ------------------------------------------------------------------ geometry (AI_INFERENCE_ARCHITECTURE §5 example)
def test_geometry_documented_example():
    W, H = G.oriented_size(4000, 3000, 6)
    assert (W, H) == (3000, 4000)
    p = G.letterbox(W, H, 640, 640)
    assert p["r_x"] == pytest.approx(0.16) and p["pad_x"] == 80 and p["pad_y"] == 0
    r = G.restore([180, 100, 280, 300], p)
    assert r["xyxy"] == pytest.approx([625, 625, 1250, 1875])
    assert r["normalised"]["x"] == pytest.approx(0.2083, abs=1e-4) and r["normalised"]["h"] == pytest.approx(0.3125)
    assert r["clipped"] is False


@pytest.mark.parametrize("o", range(1, 9))
def test_geometry_all_orientations(o):
    W, H = G.oriented_size(400, 300, o)
    assert (W, H) == ((300, 400) if o >= 5 else (400, 300))
    p = G.letterbox(W, H, 320, 320)
    box = [10, 20, 110, 120]
    assert G.restore(G.to_input(box, p), p)["xyxy"] == pytest.approx(box)


def test_geometry_stretch_clip_and_degenerate():
    p = G.letterbox(200, 100, 100, 100, mode="stretch")
    assert G.restore(G.to_input([10, 10, 50, 50], p), p)["xyxy"] == pytest.approx([10, 10, 50, 50])
    q = G.letterbox(200, 100, 100, 100)
    r = G.restore([-10, 20, 30, 40], q)
    assert r["clipped"] and r["xyxy"][0] == 0
    assert G.restore([10, 30, 10.5, 40], q) is None
    with pytest.raises(ValueError):
        G.oriented_size(10, 10, 9)


# ------------------------------------------------------------------ output contract
META = {"model_id": "VT-DET-0001", "version": "0.1.0", "readiness": "EVALUATED_LIMITED", "classes": ["dent"], "supported_classes": [], "experimental_classes": ["dent"]}
PREP = {"mode": "letterbox", "S_w": 416, "S_h": 416, "r_x": 0.65, "r_y": 0.65, "pad_x": 0.0, "pad_y": 0.0}


def test_output_contract_detections_and_thresholds():
    o = O.build(META, {"ref": "img-1", "width": 640, "height": 640}, PREP,
                [{"cls": 0, "score": 0.4, "xyxy": [10, 10, 50, 60]}, {"cls": 0, "score": 0.9, "xyxy": [100, 100, 200, 220]}, {"cls": 0, "score": 0.05, "xyxy": [1, 1, 9, 9]}],
                score_threshold={"dent": 0.2}, review_threshold={"dent": 0.5}, nms_iou=0.65)
    assert o["status"] == "detections" and [d["score"] for d in o["detections"]] == [0.9, 0.4]
    assert o["detections"][1]["low_confidence"] and not o["detections"][0]["low_confidence"]
    assert o["suppressed"]["below_threshold"] == 1 and o["input"]["coordinate_frame"] == "oriented_image_pixels"
    assert o["detections"][0]["box_normalised"]["w"] == pytest.approx(100 / 640)
    assert O.SUPPORT_WARNING in o["warnings"] and "severity" not in json.dumps(o["detections"])


def test_output_contract_no_detections_is_valid_but_not_defect_free():
    o = O.build(META, {"width": 640, "height": 480}, PREP, [], score_threshold={"dent": 0.2}, nms_iou=0.65)
    assert o["status"] == "no_detections" and O.NO_DETECTION_WARNING in o["warnings"]
    assert "does not establish that the surface is free of defects" in O.NO_DETECTION_WARNING


@pytest.mark.parametrize("det", [{"cls": 0, "score": 1.5, "xyxy": [0, 0, 5, 5]}, {"cls": 3, "score": 0.5, "xyxy": [0, 0, 5, 5]},
                                 {"cls": 0, "score": float("nan"), "xyxy": [0, 0, 5, 5]}, {"cls": 0, "score": 0.5, "xyxy": [0, 0, float("inf"), 5]}])
def test_output_contract_rejects_invalid_model_output(det):
    with pytest.raises(O.InvalidModelOutput):
        O.build(META, {"width": 64, "height": 64}, PREP, [det], score_threshold={"dent": 0.1}, nms_iou=0.65)


def test_output_validator_rejects_bad_documents():
    o = O.build(META, {"width": 640, "height": 640}, PREP, [{"cls": 0, "score": 0.9, "xyxy": [10, 10, 50, 60]}], score_threshold={"dent": 0.2}, nms_iou=0.65)
    for mutate in (lambda d: d["detections"][0].update(severity="high"), lambda d: d["detections"][0].update(box_xyxy=[10, 10, 700, 60]),
                   lambda d: d["detections"][0].update(class_name="corrosion"), lambda d: d.update(warnings=[]), lambda d: d.update(status="no_detections")):
        bad = copy.deepcopy(o); mutate(bad)
        with pytest.raises(O.InvalidModelOutput):
            O.validate(bad)


# ------------------------------------------------------------------ readiness levels
def _ev(**kw):
    base = {"data_ready": True, "run_completed": True, "thresholds_from_validation": True, "final_test_evaluation": True, "test_split_digest_matches": True,
            "per_class_metrics": True, "limitations_documented": True, "targets_preregistered": None, "targets_met": None, "missed_defect_review_done": False, "dataset_licence_cleared": False}
    return {**base, **kw}


def test_readiness_levels_follow_evidence():
    assert R.decide(_ev(data_ready=False))["level"] == "DATA_BLOCKED"
    assert R.decide(_ev(run_completed=False))["level"] is None
    assert R.decide(_ev(final_test_evaluation=False))["level"] == "EXPERIMENTAL"
    lim = R.decide(_ev())
    assert lim["level"] == "EVALUATED_LIMITED" and any("MLG-04" in r for r in lim["reasons"])
    assert R.decide(_ev(targets_preregistered={"dent": 0.5}, targets_met=False, missed_defect_review_done=True, dataset_licence_cleared=True))["level"] == "EVALUATED_LIMITED"
    assert R.decide(_ev(targets_preregistered={"dent": 0.5}, targets_met=True, missed_defect_review_done=True, dataset_licence_cleared=True))["level"] == "CANDIDATE_FOR_INTEGRATION"
