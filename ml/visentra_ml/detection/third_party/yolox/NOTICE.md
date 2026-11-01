# Vendored third-party code: YOLOX

| Item | Value |
|---|---|
| Project | YOLOX — Megvii-BaseDetection |
| Source | https://github.com/Megvii-BaseDetection/YOLOX |
| Version | tag `0.3.0`, commit `419778480ab6ec0590e5d3831b3afb3b46ab2aa3` |
| Licence | Apache License 2.0 (full text in `LICENSE` next to this file) |
| Files taken | `yolox/models/{darknet, losses, network_blocks, yolo_head, yolo_pafpn, yolox}.py`; `meshgrid` from `yolox/utils/compat.py` and `bboxes_iou` from `yolox/utils/boxes.py` (now `_utils.py`) |

## Changes made by VisionTra (Apache-2.0 §4(b))

1. `yolo_head.py`: `loguru` logger replaced by the standard `logging` module.
2. `yolo_head.py`: `from yolox.utils import …` replaced by the local `_utils.py`.
3. `yolo_head.py`: `torch.cuda.amp.autocast(enabled=False)` replaced by the equivalent, non-deprecated `torch.autocast(device_type=…, enabled=False)`.
4. `_utils.py`: `meshgrid` always passes `indexing="ij"` (the original did so for torch ≥ 1.10).

No other change. The rest of YOLOX (training framework, data loaders, evaluators, demo tools) is **not** included, and the original `setup.py` was never executed.

## Why vendored

The published `yolox` 0.3.0 package does not build on Python 3.13 / PyTorch 2.14 (its `setup.py` needs PyTorch at build time). Vendoring the model definition only also avoids its unused dependencies (loguru, thop, opencv, onnx-simplifier).
