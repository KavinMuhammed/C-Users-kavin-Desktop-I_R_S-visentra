"""YOLOX detector construction, pretrained-weight loading and inference postprocessing."""
from __future__ import annotations

import hashlib

import torch
import torchvision

from . import config as C
from .third_party.yolox import YOLOPAFPN, YOLOX, YOLOXHead


def build(variant: str, num_classes: int) -> torch.nn.Module:
    depth, width, dw = C.VARIANTS[variant]
    m = YOLOX(YOLOPAFPN(depth, width, depthwise=dw), YOLOXHead(num_classes, width, depthwise=dw))
    # YOLOX reference initialisation (yolox/exp/yolox_base.py): BN eps/momentum and prior-probability head bias
    for mod in m.modules():
        if isinstance(mod, torch.nn.BatchNorm2d):
            mod.eps, mod.momentum = 1e-3, 0.03
    m.head.initialize_biases(1e-2)
    return m


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pretrained(model: torch.nn.Module, pre: dict) -> dict:
    """Load COCO-pretrained weights after verifying their sha256. Tensors whose shape differs
    (the class-prediction convs: 80 COCO classes → our classes) are skipped and reported."""
    path = C.resolve(pre["path"], must_exist=True)
    got = sha256_file(path)
    if got != pre["sha256"]:
        raise ValueError(f"pretrained weights checksum mismatch: expected {pre['sha256']}, got {got}")
    ck = torch.load(path, map_location="cpu", weights_only=True)
    sd = ck.get("model") or ck.get("state_dict") or ck          # YOLOX release ("model") or a VisionTra bundle ("state_dict")
    own = model.state_dict()
    load = {k: v for k, v in sd.items() if k in own and own[k].shape == v.shape}
    skipped = sorted(k for k in sd if k in own and own[k].shape != sd[k].shape)
    missing = sorted(k for k in own if k not in load)
    model.load_state_dict(load, strict=False)
    return {"loaded_tensors": len(load), "skipped_shape_mismatch": skipped, "not_in_checkpoint": [k for k in missing if k not in skipped], "sha256": got}


@torch.no_grad()
def postprocess(raw: torch.Tensor, num_classes: int, *, score_floor: float, nms_iou: float, max_det: int) -> list[torch.Tensor]:
    """raw: [B, A, 5 + C] decoded YOLOX output (cx, cy, w, h, obj, cls…) in input pixels.
    Returns per image an [N, 6] tensor: x1, y1, x2, y2, score, class (class-wise NMS, top max_det)."""
    from .output import InvalidModelOutput
    if raw.ndim != 3 or num_classes < 1 or raw.shape[-1] != 5 + num_classes:
        raise InvalidModelOutput("invalid decoded detector tensor shape")
    if not torch.isfinite(raw).all():
        raise InvalidModelOutput("non-finite decoded detector output")
    if (raw[..., 2:4] < 0).any() or (raw[..., 4:] < 0).any() or (raw[..., 4:] > 1).any():
        raise InvalidModelOutput("invalid decoded dimensions or probabilities")
    out = []
    for p in raw:
        box = torch.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2, p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1)
        cls_conf, cls = p[:, 5:5 + num_classes].max(1)
        score = p[:, 4] * cls_conf
        keep = score >= score_floor
        box, score, cls = box[keep], score[keep], cls[keep]
        if box.numel():
            k = torchvision.ops.batched_nms(box, score, cls, nms_iou)[:max_det]
            box, score, cls = box[k], score[k], cls[k]
        out.append(torch.cat([box, score[:, None], cls[:, None].float()], 1) if box.numel() else torch.zeros(0, 6))
    return out


def to_original(det: torch.Tensor, meta: dict) -> list[dict]:
    """Map input-pixel boxes back to the oriented original image (AI_INFERENCE_ARCHITECTURE §5),
    clamp, flag clipping and drop boxes under 2 px."""
    W, H, r, px, py = meta["orig_w"], meta["orig_h"], meta["ratio"], meta["pad_x"], meta["pad_y"]
    res = []
    for x1, y1, x2, y2, s, c in det.tolist():
        X1, Y1, X2, Y2 = (x1 - px) / r, (y1 - py) / r, (x2 - px) / r, (y2 - py) / r
        cX1, cY1, cX2, cY2 = min(max(X1, 0), W), min(max(Y1, 0), H), min(max(X2, 0), W), min(max(Y2, 0), H)
        if cX2 - cX1 < 2 or cY2 - cY1 < 2:
            continue
        res.append({"cls": int(c), "score": float(s), "xyxy": [cX1, cY1, cX2, cY2],
                    "clipped": (cX1, cY1, cX2, cY2) != (X1, Y1, X2, Y2), "box_input_xyxy": [x1, y1, x2, y2]})
    return res
