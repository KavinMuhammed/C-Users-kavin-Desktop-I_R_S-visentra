"""Phase 2 (29 Oct): facts about the production detector that the AI review relies on.

Score semantics and NMS behaviour are tested on hand-built decoded tensors (no model weights involved). Artifact checks read
the real VT-DET-0002 bundle. Evidence and decisions: docs/PHASE2_AI_QUALITY_REPORT.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import model as M  # noqa: E402

BUNDLE = ROOT / "models/detection/VT-DET-0002"


def row(cx, cy, w, h, obj, cls):
    return [cx, cy, w, h, obj, *cls]


def test_score_is_objectness_times_highest_class_sigmoid():
    raw = torch.tensor([[row(50, 50, 20, 20, 0.5, [0.2, 0.8, 0.1])]])
    out = M.postprocess(raw, 3, score_floor=0.001, nms_iou=0.65, max_det=100)[0]
    assert out.shape == (1, 6)
    assert out[0, 4].item() == pytest.approx(0.4)            # 0.5 × 0.8, not a softmax and not the class score alone
    assert int(out[0, 5].item()) == 1                         # only the winning class is kept


def test_nms_is_class_wise_so_overlapping_boxes_of_different_classes_coexist():
    raw = torch.tensor([[row(50, 50, 20, 20, 0.9, [0.0, 0.9, 0.0]), row(51, 50, 20, 20, 0.9, [0.6, 0.0, 0.0])]])
    out = M.postprocess(raw, 3, score_floor=0.001, nms_iou=0.65, max_det=100)[0]
    assert sorted(int(c) for c in out[:, 5].tolist()) == [0, 1]


def test_nested_same_class_boxes_of_different_size_survive_iou_nms():
    """Error analysis finding: a large and a small box of the same class (IoU < 0.65) are both kept."""
    raw = torch.tensor([[row(50, 50, 60, 60, 0.9, [0.0, 0.6, 0.0]), row(50, 50, 20, 20, 0.9, [0.0, 0.5, 0.0])]])
    out = M.postprocess(raw, 3, score_floor=0.001, nms_iou=0.65, max_det=100)[0]
    assert out.shape[0] == 2


def test_production_bundle_is_intact_and_selected():
    if not (BUNDLE / "model.pt").is_file():
        pytest.skip("VT-DET-0002 weights not present")
    from visentra_ml.detection import artifact as A
    v = A.verify(BUNDLE)
    assert v["ok"], v["problems"]
    meta = json.loads((BUNDLE / "metadata.json").read_text())
    assert meta["files"]["model.pt"]["sha256"] == "913906b678e9f26cb3d48a21d315cd6466a715a4433c12c6ebe48cd28af33dad"
    assert meta["thresholds"]["score_threshold"] == {"crack": 0.025, "dent": 0.025, "corrosion": 0.275}   # unchanged by Phase 2
    assert meta["input_size"] == [416, 416] and meta["preprocessing"]["colour_order"] == "BGR"


def test_rejected_containment_suppression_keeps_other_classes_and_removes_nested_boxes():
    import postprocess_study as S
    p = [(1, 0.6, [0, 0, 100, 100]), (1, 0.5, [10, 10, 30, 30]), (0, 0.4, [10, 10, 30, 30])]
    kept = S.contain_suppress(p, 0.8)
    assert (1, 0.5, [10, 10, 30, 30]) not in kept and (0, 0.4, [10, 10, 30, 30]) in kept and len(kept) == 2
