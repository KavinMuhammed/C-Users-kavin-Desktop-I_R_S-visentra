"""Audit the detection dataset by framing (whole/medium aircraft vs close-up) and defect scale.

    python scripts/ml/audit_scale_framing.py --config ml/configs/detection/VT-EXP-006.json --out experiments/audit/scale_framing.json

There is no framing metadata in the source datasets, so framing is a *measured proxy*: the COCO-pretrained YOLOX-Tiny
(the gate's backbone) gives an 'airplane' score per image. >= 0.5: a recognisable aircraft is in view (whole/medium
distance); 0.1-0.5: partial; < 0.1: close-up of a surface. One image per photo group (augmented copies would inflate
counts). Boxes are bucketed by relative area. Nothing is relabelled.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
from visentra_ml.detection import domain_gate as G  # noqa: E402


def framing(a: float) -> str:
    return "whole_or_medium" if a >= 0.5 else "partial" if a >= 0.1 else "close_up"


def size_bucket(r: float) -> str:
    return "tiny<0.1%" if r < 0.001 else "small<1%" if r < 0.01 else "medium<5%" if r < 0.05 else "large<25%" if r < 0.25 else "huge>=25%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json")
    ap.add_argument("--out", default="experiments/audit/scale_framing.json")
    a = ap.parse_args()
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg); cls = mp["targets"]
    grp = {}
    for s in ("train", "valid", "test"):
        for r in csv.DictReader(open(ROOT / f"datasets/splits/VT-PREP-001/{s}.csv", encoding="utf-8")):
            grp[r["file_id"]] = (r["dataset_id"], r["photo_group"])
    fx = G.FeatureExtractor(ROOT / "models/gate/VT-GATE-0001/coco.pth")
    out = {"method": __doc__.strip().splitlines()[2:6], "splits": {}}
    for s in ("train", "valid", "test"):
        items, _ = Dt.load_items(cfg, s, mp)
        one = {}
        for it in sorted(items, key=lambda i: i.file_id):
            one.setdefault(grp.get(it.file_id, (None, it.file_id)), it)
        reps = list(one.values())
        fr, boxes = collections.Counter(), collections.Counter()
        groups = {}
        per_ds = collections.defaultdict(collections.Counter)
        for k in range(0, len(reps), 16):
            chunk = reps[k:k + 16]
            ims = []
            for it in chunk:
                with Image.open(C.resolve(it.rel_path)) as im:
                    im.load(); ims.append(im.copy())
            f = fx.features(ims)
            for it, row in zip(chunk, f):
                fm = framing(float(row[-80 + G.COCO_AIRPLANE]))
                g = grp.get(it.file_id, ("?", it.file_id))
                groups["|".join(g)] = {"framing": fm, "airplane": round(float(row[-80 + G.COCO_AIRPLANE]), 4),
                                       "classes": sorted({cls[b.cls] for b in it.boxes})}
                fr[fm] += 1; per_ds[grp.get(it.file_id, ("?",))[0]][fm] += 1
                for b in it.boxes:
                    boxes[(cls[b.cls], fm, size_bucket((b.x2 - b.x1) * (b.y2 - b.y1)))] += 1
        out["splits"][s] = {"photo_groups": len(reps), "framing": dict(fr), "framing_by_dataset": {k: dict(v) for k, v in per_ds.items()},
                            "boxes_by_class_framing_size": {"|".join(k): v for k, v in sorted(boxes.items())},
                            "photo_groups_framing": groups}
        print(s, len(reps), dict(fr), flush=True)
    p = ROOT / a.out; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(out, indent=1))
    print("wrote", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
