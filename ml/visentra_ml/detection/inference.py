"""Single-image inference for a packaged detector (AI_INFERENCE_ARCHITECTURE §5–6).

One code path for the prediction CLI, the integration fixture and Phase 8 experimental inference:
EXIF orientation → letterbox (fill from metadata) → BGR 0–255 → model → class-wise NMS → restore to
oriented-image pixels → ``visiontra.detection_output/1``. Inference is single-threaded and runs under
``torch.inference_mode`` (no gradients) so repeated runs are bit-identical on the same machine.

This module only predicts. It never writes files, never touches a database and never decides what a
user may see: that is the caller's job (see ``experimental.py`` for the development-only mode).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

from . import artifact as A
from . import geometry as G
from . import model as Mo
from . import output as O


class Predictor:
    def __init__(self, bundle: Path, *, threads: int | None = 1):
        if threads:                                            # None: keep the process-wide setting (API server)
            torch.set_num_threads(threads)
        self.model, self.meta = A.load(Path(bundle))          # verifies every checksum, weights_only, eval mode
        self.bundle = Path(bundle)

    def preprocess(self, im: Image.Image) -> tuple[torch.Tensor, dict, dict]:
        orientation = (im.getexif() or {}).get(0x0112, 1) or 1
        im = ImageOps.exif_transpose(im).convert("RGB")
        W, H = im.size
        sh, sw = self.meta["input_size"]
        p = G.letterbox(W, H, sw, sh)
        nw, nh = round(W * p["r_x"]), round(H * p["r_y"])
        canvas = np.full((sh, sw, 3), int(self.meta["preprocessing"].get("fill", 114)), dtype=np.uint8)
        ox, oy = int(round(p["pad_x"])), int(round(p["pad_y"]))
        canvas[oy:oy + nh, ox:ox + nw] = np.asarray(im.resize((nw, nh), Image.BILINEAR))[:, :, ::-1]
        x = torch.from_numpy(np.ascontiguousarray(canvas.transpose(2, 0, 1))).float()[None]
        return x, {**p, "pad_x": ox, "pad_y": oy}, {"width": W, "height": H, "orientation": orientation}

    def raw_detections(self, im: Image.Image, timing: dict | None = None) -> tuple[list[dict], dict, dict]:
        """NMS-filtered detections (score ≥ 0.001) restored to oriented-image pixels, before thresholds.
        ``timing`` (optional) receives preprocess_ms, model_ms and postprocess_ms."""
        t0 = time.perf_counter()
        x, p, info = self.preprocess(im)
        t1 = time.perf_counter()
        with torch.inference_mode():
            raw = self.model(x)
        t2 = time.perf_counter()
        det = Mo.postprocess(raw, len(self.meta["classes"]), score_floor=0.001,
                             nms_iou=self.meta["thresholds"]["nms_iou"], max_det=100)[0]
        dets, degenerate = [], 0
        for x1, y1, x2, y2, s, c in det.tolist():
            r = G.restore([x1, y1, x2, y2], p)
            if r:
                dets.append({"cls": int(c), "score": float(s), "xyxy": r["xyxy"], "clipped": r["clipped"]})
            else:
                degenerate += 1
        if timing is not None:
            timing.update(preprocess_ms=(t1 - t0) * 1000, model_ms=(t2 - t1) * 1000, postprocess_ms=(time.perf_counter() - t2) * 1000,
                          raw_detections=len(dets), degenerate=degenerate)
        return dets, p, info

    def predict(self, im: Image.Image, ref: str, timing: dict | None = None) -> dict:
        dets, p, info = self.raw_detections(im, timing)
        th = self.meta["thresholds"]
        out = O.build(self.meta, {"ref": ref, **info}, p, dets, score_threshold=th["score_threshold"],
                      review_threshold=th["review_threshold"], nms_iou=th["nms_iou"])
        if timing is not None:
            out["suppressed"]["degenerate"] = timing.get("degenerate", 0)   # boxes < 2 px after restoration (§5)
        return out

    def predict_file(self, path: Path, ref: str | None = None) -> dict:
        with Image.open(path) as im:
            im.load()
            return self.predict(im, ref or Path(path).name)
