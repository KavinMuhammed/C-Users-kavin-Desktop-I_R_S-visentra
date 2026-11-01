"""Does tiled inference help small defects? Validation-split comparison of full-image vs full-image + tiles.

    python scripts/ml/evaluate_tiled.py --bundle models/detection/VT-DET-0002 [--grid 2 --overlap 0.2] [--limit N]

Tiling: the full image plus an N×N grid of overlapping tiles, each letterboxed to the model's input size, boxes mapped
back to image pixels and merged with class-wise NMS. It costs (1 + N²)× inference time. Thresholds are the bundle's
(chosen on validation); the test split is never used here. Writes experiments/tiling/<bundle>-<stamp>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image, ImageOps
from torchvision.ops import batched_nms

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
from visentra_ml.detection import engine as E  # noqa: E402
from visentra_ml.detection.inference import Predictor  # noqa: E402


def tiles(W: int, H: int, n: int, overlap: float):
    tw, th = W / (n - (n - 1) * overlap), H / (n - (n - 1) * overlap)
    sx, sy = tw * (1 - overlap), th * (1 - overlap)
    for j in range(n):
        for i in range(n):
            x0, y0 = round(i * sx), round(j * sy)
            yield x0, y0, min(W, round(x0 + tw)), min(H, round(y0 + th))


def predict_tiled(P: Predictor, im: Image.Image, n: int, overlap: float, nms_iou: float):
    im = ImageOps.exif_transpose(im).convert("RGB")
    W, H = im.size
    boxes, scores, cls = [], [], []
    views = [(0, 0, W, H)] + (list(tiles(W, H, n, overlap)) if n > 1 else [])
    for x0, y0, x1, y1 in views:
        crop = im if (x0, y0, x1, y1) == (0, 0, W, H) else im.crop((x0, y0, x1, y1))
        dets, _, _ = P.raw_detections(crop)
        for d in dets:
            a, b, c, e = d["xyxy"]
            # drop boxes cut by an inner tile edge; the full image or a neighbouring tile has them whole
            inner = (x0 > 0 and a <= 1) or (y0 > 0 and b <= 1) or (x1 < W and c >= (x1 - x0) - 1) or (y1 < H and e >= (y1 - y0) - 1)
            if inner and (x0, y0, x1, y1) != (0, 0, W, H):
                continue
            boxes.append([a + x0, b + y0, c + x0, e + y0]); scores.append(d["score"]); cls.append(d["cls"])
    if not boxes:
        return []
    bt, st, ct = torch.tensor(boxes, dtype=torch.float32), torch.tensor(scores), torch.tensor(cls)
    keep = batched_nms(bt, st, ct, nms_iou)[:100]
    return [(int(ct[k]), float(st[k]), bt[k].tolist()) for k in keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json", help="only for the validation item list")
    ap.add_argument("--grid", type=int, default=2)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    P = Predictor(ROOT / a.bundle, threads=None)
    if torch.cuda.is_available():
        P.model = P.model.cuda()
        _orig = P.model.forward
        P.model.forward = lambda x, *r, **k: _orig(x.cuda(), *r, **k).cpu()
    cfg = json.loads((ROOT / a.config).read_text())
    mp = Dt.load_mapping(cfg); classes = mp["targets"]
    assert classes == P.meta["classes"], "class order differs between bundle and config"
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    items = items[: a.limit] if a.limit else items
    th = P.meta["thresholds"]
    out = {"bundle": a.bundle, "split": "valid", "images": len(items), "grid": a.grid, "overlap": a.overlap, "split_digest": Dt.split_digest(items)}
    for mode, n in (("full", 1), (f"full+{a.grid}x{a.grid}", a.grid)):
        gts, preds, sizes = {}, {}, {}
        t0 = time.time()
        for k, it in enumerate(items):
            with Image.open(C.resolve(it.rel_path)) as im:
                im.load(); W, H = ImageOps.exif_transpose(im).size
                preds[it.file_id] = predict_tiled(P, im, n, a.overlap, th["nms_iou"])
            sizes[it.file_id] = (W, H)
            gts[it.file_id] = [(b.cls, [b.x1 * W, b.y1 * H, b.x2 * W, b.y2 * H]) for b in it.boxes]
            if (k + 1) % 100 == 0:
                print(f"  {mode}: {k + 1}/{len(items)}", flush=True)
        vm = E.evaluate_preds(gts, preds, sizes, classes, match_iou=th["match_iou"], score_threshold=th["score_threshold"])
        out[mode] = {"AP50": vm["AP50"], "AP50_95": vm["AP50_95"], "seconds": round(time.time() - t0, 1),
                     "per_class_ap": {c: {k: vm["per_class_ap"][c].get(k) for k in ("AP50", "AP_small", "AP_medium", "AP_large")} for c in classes},
                     "per_class_at_threshold": vm["per_class_at_threshold"]}
        print(mode, json.dumps({k: out[mode][k] for k in ("AP50", "AP50_95", "seconds")}), flush=True)
    dst = ROOT / "experiments" / "tiling"; dst.mkdir(parents=True, exist_ok=True)
    f = dst / f"{Path(a.bundle).name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    f.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", f.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
