"""Nose-damage hard-case recovery: VT-HC-001 contract, loader, leakage rules, evaluation geometry."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
HC = ROOT / "datasets/hard_cases/VT-HC-001"
FREE = ("Public domain", "CC0", "CC BY ", "CC BY-SA")


def ann():
    return json.loads((HC / "annotations.json").read_text())


def manifest():
    return list(csv.DictReader(open(HC / "manifest.csv", encoding="utf-8")))


def test_every_image_has_provenance_licence_and_matching_checksum():
    rows = manifest()
    assert len(rows) == len(ann()["images"])
    for r in rows:
        assert r["source"] == "Wikimedia Commons" and r["page"].startswith("https://commons.wikimedia.org/wiki/File:")
        assert r["licence"].startswith(FREE), r["licence"]
        assert hashlib.sha256((ROOT / r["file"]).read_bytes()).hexdigest() == r["sha256"]


def test_training_split_is_public_domain_or_cc0_only():
    for r in manifest():
        if r["split"] == "train":
            assert r["licence"] in ("Public domain", "CC0"), (r["file"], r["licence"])


def test_boxes_are_inside_images_and_use_target_classes():
    for rel, v in ann()["images"].items():
        for b in v["boxes"]:
            x1, y1, x2, y2 = b["xyxy"]
            assert 0 <= x1 < x2 <= v["width"] and 0 <= y1 < y2 <= v["height"], rel
            assert b["cls"] in ("crack", "dent", "corrosion") and b["label_quality"] in ("clear", "probable")
        if v["status"] != "labelled":
            assert not v["boxes"] and v["split"] == "none", rel   # ambiguous / excluded images are never labelled or used


def test_event_groups_never_cross_train_and_eval():
    by = {}
    for v in ann()["images"].values():
        if v["split"] in ("train", "eval"):
            by.setdefault(v["event_group"], set()).add(v["split"])
    assert all(len(s) == 1 for s in by.values()), by


def test_loader_returns_only_labelled_train_items_normalised():
    from visentra_ml.detection import data as Dt
    cfg = json.loads((ROOT / "ml/configs/detection/VT-EXP-008.json").read_text())
    items = Dt.load_hard_cases(cfg, "train", ["crack", "dent", "corrosion"])
    a = ann()["images"]
    assert items and all(a[i.rel_path]["split"] == "train" and a[i.rel_path]["status"] == "labelled" for i in items)
    assert all(0 <= b.x1 < b.x2 <= 1 and 0 <= b.y1 < b.y2 <= 1 for i in items for b in i.boxes)
    assert not Dt.load_hard_cases(cfg, "valid", ["crack", "dent", "corrosion"])            # no eval leakage via 'valid'
    with pytest.raises(Dt.DataError):
        Dt.load_hard_cases(cfg, "train", ["crack", "corrosion"])                           # wrong class mapping fails loudly


def test_oversample_groups_exist_in_train_split():
    groups = set(json.loads((ROOT / "ml/configs/detection/oversample_whole_medium_dent_groups.json").read_text())["groups"])
    train = {f"{r['dataset_id']}|{r['photo_group']}" for r in csv.DictReader(open(ROOT / "datasets/splits/VT-PREP-001/train.csv", encoding="utf-8"))}
    assert groups and groups <= train


def test_iou_and_centre_rule():
    import evaluate_hard_cases as H
    assert H.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert H.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert abs(H.iou([0, 0, 10, 10], [5, 0, 15, 10]) - 1 / 3) < 1e-9


CLEAN = ROOT / "datasets/hard_cases/VT-CLEAN-001"


def test_clean_reference_set_provenance_and_never_trained():
    rows = list(csv.DictReader(open(CLEAN / "manifest.csv", encoding="utf-8")))
    assert len(rows) >= 20 and {r["status"] for r in rows} <= {"clean", "excluded"}
    for r in rows:
        assert r["page"].startswith("https://commons.wikimedia.org/wiki/File:") and r["licence"].startswith(FREE + ("CC BY",))
        assert hashlib.sha256((ROOT / r["file"]).read_bytes()).hexdigest() == r["sha256"]
    for cfg in (ROOT / "ml/configs/detection").glob("VT-EXP-*.json"):
        assert "VT-CLEAN-001" not in cfg.read_text(), f"{cfg.name} must not train on the clean evaluation set"


def test_evaluation_ledger_covers_every_hard_case_and_probe():
    led = list(csv.DictReader(open(HC / "evaluation_usage.csv", encoding="utf-8")))
    by = {r["image"]: r for r in led}
    for r in manifest():
        assert Path(r["file"]).name in by
    ev = [r for r in led if r["split"] == "eval"]
    probes = [r for r in led if r["split"] == "probe"]
    assert len(ev) == 6 and len(probes) == 3
    assert sum(int(r["boxes"]) for r in ev + probes) == 11          # 9 images, 11 labelled defects
    assert all("NOT" in r["role"] for r in ev + probes)              # never presented as an untouched test set
