"""Aircraft-surface domain gate (Phase 13).

Decides, before defect detection, whether an image shows an aircraft or an aircraft surface. Images that do not
(a person, an animal, a car, a document, a screenshot …) are saved but not analysed for defects, so the detector
never reports a "crack" on a cat.

How it decides
--------------
1. A COCO-pretrained YOLOX-Tiny (Apache-2.0, MS COCO 80 classes) runs once at 320 px. A forward hook keeps the
   backbone's dark4/dark5 feature maps; the head gives the highest score per COCO class.
2. Features = [mean- and max-pooled dark4 and dark5 activations, the 80 per-class maximum scores] (1,232 values).
3. A logistic regression (weights in ``gate.json``; no pickled code) gives p(aircraft surface).
4. The image is in domain when p ≥ the threshold chosen on the gate's validation split, **or** the COCO detector
   finds an airplane (class 4) with score ≥ ``airplane_min_score`` (whole-aircraft photos, ramp and hangar shots).
5. VT-GATE-0002 (AI recovery): when ``gate.json`` has an ``uncertain`` band, five overlapping crops are scored too
   (close-ups of a nose or a panel), and the result has three states: relevant · uncertain · irrelevant. An
   *uncertain* image is analysed and flagged for the engineer to confirm; only *irrelevant* skips detection. The band
   is calibrated on the gate's validation split (scripts/ml/calibrate_gate_uncertain.py).

The gate is a separate bundle (``models/gate/<GATE_ID>/``: ``coco.pth``, ``gate.json``, ``SHA256SUMS``); every
checksum is verified before loading.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

from . import model as Mo

COCO_AIRPLANE = 4
INPUT = 320
FEATURE_VERSION = "pool(dark4,dark5)+coco80max@320/v1"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def build_coco(weights: Path) -> torch.nn.Module:
    m = Mo.build("tiny", 80)
    ck = torch.load(weights, map_location="cpu", weights_only=True)
    m.load_state_dict(ck.get("model", ck), strict=True)
    m.eval()
    return m


class FeatureExtractor:
    def __init__(self, coco_weights: Path, threads: int | None = None):
        if threads:
            torch.set_num_threads(threads)
        self.model = build_coco(Path(coco_weights))
        self._feats: dict = {}
        self.model.backbone.backbone.register_forward_hook(lambda _m, _i, out: self._feats.update(out))

    @staticmethod
    def prepare(im: Image.Image) -> torch.Tensor:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        r = INPUT / max(w, h)
        nw, nh = max(1, round(w * r)), max(1, round(h * r))
        canvas = np.full((INPUT, INPUT, 3), 114, dtype=np.uint8)
        canvas[:nh, :nw] = np.asarray(im.resize((nw, nh), Image.BILINEAR))[:, :, ::-1]
        return torch.from_numpy(np.ascontiguousarray(canvas.transpose(2, 0, 1))).float()

    @torch.inference_mode()
    def features(self, images: list[Image.Image]) -> np.ndarray:
        x = torch.stack([self.prepare(im) for im in images])
        self._feats.clear()
        out = self.model(x)                                     # [B, A, 85] decoded: cx, cy, w, h, obj, 80 class probs
        cls = (out[..., 4:5] * out[..., 5:]).amax(1)            # [B, 80] best score per COCO class
        parts = []
        for k in ("dark4", "dark5"):
            f = self._feats[k]
            parts += [f.mean((2, 3)), f.amax((2, 3))]
        return torch.cat(parts + [cls], 1).numpy().astype(np.float64)


class Gate:
    def __init__(self, bundle: Path, threads: int | None = None):
        bundle = Path(bundle)
        sums = {}
        for line in (bundle / "SHA256SUMS").read_text().splitlines():
            h, name = line.split("  ", 1)
            sums[name] = h
        if not {"gate.json", "coco.pth"}.issubset(sums):
            raise ValueError("domain gate checksum manifest is incomplete")
        for name, h in sums.items():
            if _sha(bundle / name) != h:
                raise ValueError(f"domain gate file {name} failed checksum verification")
        self.spec = json.loads((bundle / "gate.json").read_text())
        if self.spec.get("feature_version") != FEATURE_VERSION:
            raise ValueError("domain gate feature version mismatch")
        self.fx = FeatureExtractor(bundle / "coco.pth", threads)
        self.mean = np.asarray(self.spec["mean"]); self.std = np.asarray(self.spec["std"])
        self.w = np.asarray(self.spec["weights"]); self.b = float(self.spec["bias"])
        self.threshold = float(self.spec["threshold"]); self.airplane_min = float(self.spec["airplane_min_score"])
        self.gate_id = self.spec["gate_id"]

    def probability(self, feats: np.ndarray) -> np.ndarray:
        if feats.ndim != 2 or feats.shape[1] != 1232 or not np.isfinite(feats).all():
            raise ValueError("invalid domain gate features")
        z = ((feats - self.mean) / self.std) @ self.w + self.b
        if not np.isfinite(z).all():
            raise ValueError("invalid domain gate logits")
        return 1 / (1 + np.exp(-np.clip(z, -40, 40)))

    @staticmethod
    def views(im: Image.Image, frac: float = 0.6) -> list[Image.Image]:
        """Five overlapping crops (four corners + centre, each ``frac`` of the width/height). A close-up of a nose or a
        panel can fill the frame so that no single global view looks like an aircraft; a crop often does."""
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        cw, ch = max(1, round(w * frac)), max(1, round(h * frac))
        boxes = [(0, 0), (w - cw, 0), (0, h - ch), (w - cw, h - ch), ((w - cw) // 2, (h - ch) // 2)]
        return [im.crop((x, y, x + cw, y + ch)) for x, y in boxes]

    def evidence(self, im: Image.Image) -> dict:
        """Full-image and multi-crop scores (6 forward passes, one batch)."""
        f = self.fx.features([im] + self.views(im))
        p = self.probability(f)
        air = f[:, -80 + COCO_AIRPLANE]
        coco = f[0, -80:]
        top = int(np.argmax(coco))
        return {"p": float(p[0]), "airplane": float(air[0]), "p_crop_max": float(p[1:].max()), "airplane_crop_max": float(air[1:].max()),
                "coco_top": COCO_NAMES[top], "coco_top_score": float(coco[top])}

    def decide(self, ev: dict) -> tuple[str, str]:
        """relevant | uncertain | irrelevant. Uncertain images are analysed (never silently discarded) and flagged."""
        scores = [ev[k] for k in ("p", "airplane", "p_crop_max", "airplane_crop_max", "coco_top_score")]
        if not all(np.isfinite(v) and 0 <= v <= 1 for v in scores):
            raise ValueError("invalid domain gate scores")
        u = self.spec.get("uncertain") or {}
        if ev["p"] >= self.threshold:
            return "relevant", "aircraft surface"
        if ev["airplane"] >= self.airplane_min:
            return "relevant", "aircraft detected"
        if u and (ev["p_crop_max"] >= self.threshold or ev["airplane_crop_max"] >= self.airplane_min):
            return "relevant", "aircraft surface (close-up: recognised in part of the image)"
        if u and (max(ev["p"], ev["p_crop_max"]) >= u["p_min"] or max(ev["airplane"], ev["airplane_crop_max"]) >= u["airplane_min"]):
            return "uncertain", "possibly an aircraft surface: analysed, but please confirm the image is relevant"
        # The COCO top class is a general-object guess (an engine intake reads as "donut"); it is reported in the evidence
        # fields but never given as the reason, because it is not evidence that the image is not an aircraft surface.
        return "irrelevant", "no aircraft evidence found in the whole image or any crop"

    def check(self, im: Image.Image) -> dict:
        if self.spec.get("uncertain"):
            ev = self.evidence(im)
            state, reason = self.decide(ev)
            return {"gate_id": self.gate_id, "decision": state, "in_domain": state != "irrelevant", "uncertain": state == "uncertain",
                    "p_aircraft_surface": round(ev["p"], 4), "p_aircraft_surface_crops": round(ev["p_crop_max"], 4), "threshold": self.threshold,
                    "coco_airplane_score": round(ev["airplane"], 4), "coco_airplane_score_crops": round(ev["airplane_crop_max"], 4),
                    "coco_top_class": ev["coco_top"], "coco_top_score": round(ev["coco_top_score"], 4), "reason": reason}
        return self._check_v1(im)

    def _check_v1(self, im: Image.Image) -> dict:
        f = self.fx.features([im])
        p = float(self.probability(f)[0])
        airplane = float(f[0, -80 + COCO_AIRPLANE])
        coco = f[0, -80:]
        top = int(np.argmax(coco))
        in_domain = p >= self.threshold or airplane >= self.airplane_min
        return {"gate_id": self.gate_id, "decision": "relevant" if in_domain else "irrelevant", "in_domain": bool(in_domain), "uncertain": False, "p_aircraft_surface": round(p, 4), "threshold": self.threshold,
                "coco_airplane_score": round(airplane, 4), "coco_top_class": COCO_NAMES[top], "coco_top_score": round(float(coco[top]), 4),
                "reason": ("aircraft surface" if p >= self.threshold else "aircraft detected") if in_domain
                else "no aircraft evidence found"}


COCO_NAMES = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant", "stop sign",
              "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
              "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
              "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
              "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv",
              "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
              "scissors", "teddy bear", "hair drier", "toothbrush"]
