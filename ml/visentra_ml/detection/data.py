"""Detection data: split manifests, approved class mapping, YOLO label parsing, readiness checks
and the training/evaluation dataset.

Rules enforced here (Phase 4/7):
- Only manifest rows of the configured datasets are used; quarantined files are not in the split
  manifests, so they can never enter training or evaluation.
- Source labels become a target class **only** through an entry of the approved class mapping
  whose status is in ``mapping_statuses_allowed``. UNRESOLVED and NOT_MAPPED classes are dropped
  (their pixels are ordinary background for the target classes) and counted.
- An image whose label file is empty is UNLABELLED, never a negative.
- Raw files are only read, never written.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
BOX_TOL = 1e-6          # same tolerance as the Phase 4 annotation validator
MIN_BOX_PX = 2.0


class DataError(ValueError):
    pass


# ------------------------------------------------------------------ class mapping
def load_mapping(cfg: dict) -> dict:
    """Return {(dataset, source_class_id): target_index} for approved entries, plus bookkeeping."""
    ds = cfg["dataset"]
    m = json.loads(C.resolve(ds["class_mapping"], must_exist=True).read_text(encoding="utf-8"))
    targets = list(ds["target_classes"])
    allowed = set(ds["mapping_statuses_allowed"])
    names = [t["name"] for t in m.get("target_classes", [])]
    unknown = [t for t in targets if t not in names]
    if unknown:
        raise DataError(f"target classes {unknown} are not in mapping {m.get('mapping_id')} targets {names}")
    use, dropped = {}, {}
    for e in m["entries"]:
        if e["dataset"] not in ds["datasets"]:
            continue
        key = (e["dataset"], int(e["source_class_id"]))
        if e.get("target") in targets and e["status"] in allowed:
            use[key] = targets.index(e["target"])
        else:
            dropped[key] = {"source_name": e["source_name"], "target": e.get("target"), "status": e["status"]}
    supported = sorted({targets[i] for i in use.values()})
    missing = [t for t in targets if t not in supported]
    if missing:
        raise DataError(f"no approved mapping provides {missing} for datasets {ds['datasets']} "
                        f"(statuses allowed: {sorted(allowed)}). Do not train these classes.")
    return {"mapping_id": m.get("mapping_id"), "version": m.get("version"), "taxonomy": m.get("taxonomy"),
            "targets": targets, "use": use, "dropped": dropped,
            "sha256": hashlib.sha256(C.resolve(ds["class_mapping"]).read_bytes()).hexdigest()}


# ------------------------------------------------------------------ labels
@dataclass
class Box:
    cls: int            # target index
    x1: float
    y1: float
    x2: float
    y2: float           # normalised [0,1], xyxy


@dataclass
class Item:
    file_id: str
    dataset_id: str
    rel_path: str
    label_path: str
    sha256: str
    asset_id: str
    photo_group: str
    eval_representative: str
    boxes: list = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)
    unlabelled: bool = False


def label_path_for(rel_path: str) -> str:
    p = Path(rel_path)
    parts = list(p.parts)
    if "images" not in parts:
        raise DataError(f"cannot derive label path for {rel_path}")
    i = len(parts) - 1 - parts[::-1].index("images")
    parts[i] = "labels"
    return str(Path(*parts).with_suffix(".txt"))


def parse_yolo(text: str, dataset_id: str, mapping: dict, *, where: str = "") -> tuple[list[Box], Counter, list[str]]:
    """Parse a YOLO txt label (boxes ``c cx cy w h`` or polygons ``c x1 y1 x2 y2 …``).

    Returns (target boxes, dropped-class counter, problems). Polygons become their bounding box.
    Values outside [0,1] beyond BOX_TOL are problems, never silently clipped (Phase 4 rule).
    """
    boxes, dropped, problems = [], Counter(), []
    for n, line in enumerate(text.splitlines(), 1):
        f = line.split()
        if not f:
            continue
        try:
            c = int(f[0]); vals = [float(v) for v in f[1:]]
        except ValueError:
            problems.append(f"{where}:{n}: not numeric"); continue
        if not all(math.isfinite(v) for v in vals):
            problems.append(f"{where}:{n}: non-finite value"); continue
        if len(vals) == 4:
            cx, cy, w, h = vals; x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        elif len(vals) >= 6 and len(vals) % 2 == 0:
            xs, ys = vals[0::2], vals[1::2]; x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        else:
            problems.append(f"{where}:{n}: wrong field count {len(vals) + 1}"); continue
        if min(x1, y1) < -BOX_TOL or max(x2, y2) > 1 + BOX_TOL:
            problems.append(f"{where}:{n}: box outside the image"); continue
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            problems.append(f"{where}:{n}: degenerate box"); continue
        x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2)   # only the BOX_TOL rounding
        key = (dataset_id, c)
        if key in mapping["use"]:
            boxes.append(Box(mapping["use"][key], x1, y1, x2, y2))
        else:
            dropped[mapping["dropped"].get(key, {}).get("source_name", f"class {c} (unknown)")] += 1
            if key not in mapping["dropped"]:
                problems.append(f"{where}:{n}: class id {c} is not in the class mapping")
    return boxes, dropped, problems


def read_split(cfg: dict, split: str) -> list[dict]:
    path = C.resolve(cfg["dataset"]["splits"][split], must_exist=True)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [r for r in rows if r["dataset_id"] in cfg["dataset"]["datasets"]]


def _asset_index(cfg: dict) -> dict:
    """file_id → asset_id from the Phase 4 derived manifest (same folder level as the splits)."""
    prep = cfg["dataset"]["prep_id"]
    p = C.resolve(f"datasets/processed/{prep}/derived_manifest.csv")
    if not p.is_file():
        return {}
    return {r["file_id"]: r["asset_id"] for r in csv.DictReader(p.open(encoding="utf-8"))}


def load_items(cfg: dict, split: str, mapping: dict, *, subset: str = "all", strict: bool = True) -> tuple[list[Item], list[str]]:
    rows = read_split(cfg, split)
    assets = _asset_index(cfg)
    if subset == "eval_representatives":
        rows = [r for r in rows if r.get("eval_representative") == "yes"]
    elif subset == "one_file_per_asset":
        # deterministic: the publisher's own valid/test copy if present (not augmented), else the first file id
        first = {}
        for r in sorted(rows, key=lambda r: (r.get("source_split") == "train", r["file_id"])):
            first.setdefault(assets.get(r["file_id"], r["file_id"]), r)
        rows = sorted(first.values(), key=lambda r: r["file_id"])
    items, problems = [], []
    for r in rows:
        rel = r["rel_path"]
        try:
            img = C.resolve(rel)
            lab = C.resolve(label_path_for(rel))
        except (C.ConfigError, DataError) as e:
            problems.append(f"{r['file_id']}: {e}"); continue
        if Path(rel).suffix.lower() not in IMG_EXT:
            problems.append(f"{r['file_id']}: unsupported image extension"); continue
        if not img.is_file():
            problems.append(f"{r['file_id']}: image missing ({rel})"); continue
        if not lab.is_file():
            problems.append(f"{r['file_id']}: label file missing"); continue
        text = lab.read_text(encoding="utf-8")
        boxes, dropped, pr = parse_yolo(text, r["dataset_id"], mapping, where=r["file_id"])
        problems += pr
        it = Item(r["file_id"], r["dataset_id"], rel, str(lab.relative_to(C.ROOT)), r.get("sha256", ""), assets.get(r["file_id"], r["file_id"]),
                  r.get("photo_group", ""), r.get("eval_representative", ""), boxes, dropped, unlabelled=not text.strip())
        items.append(it)
    if strict and problems:
        raise DataError(f"{len(problems)} data problem(s) in split {split}; first: {problems[:5]}")
    return items, problems


def load_background(cfg: dict, split: str = "train") -> list[Item]:
    """Phase 13: negative images (no defects; mostly not aircraft) used as background-only training images, so the
    detector learns to stay silent on them. Manifests list file, sha256, split; only rows of `split` are used."""
    import csv as _csv
    bg = (cfg.get("dataset") or {}).get("background") or {}
    out = []
    for man in bg.get("manifests", []):
        for r in _csv.DictReader(open(C.resolve(man, must_exist=True), encoding="utf-8")):
            if r.get("split") != split or r.get("aircraft") == "ambiguous":
                continue
            out.append(Item(f"BG:{r['file']}", "BACKGROUND", r["file"], "", r.get("sha256", ""), f"BG:{r['file']}", "", "", [], Counter(), unlabelled=False))
    return out


def load_hard_cases(cfg: dict, split: str, classes: list[str], *, qualities=("clear", "probable")) -> list[Item]:
    """Nose-damage recovery: reviewed hard cases (``datasets/hard_cases/<id>/annotations.json``). Only images with
    status 'labelled' and the requested split are used; ambiguous/excluded images never are. Boxes are stored in
    image pixels and normalised here; a class outside ``classes`` is an error (never silently dropped)."""
    hc = (cfg.get("dataset") or {}).get("hard_cases") or {}
    if not hc.get("annotations"):
        return []
    ann = json.loads(C.resolve(hc["annotations"], must_exist=True).read_text())
    out = []
    for rel, v in sorted(ann["images"].items()):
        if v.get("status") != "labelled" or v.get("split") != split:
            continue
        W, H = v["width"], v["height"]
        boxes = []
        for b in v["boxes"]:
            if b.get("label_quality") not in qualities:
                continue
            if b["cls"] not in classes:
                raise DataError(f"hard case {rel}: class {b['cls']!r} is not a target class {classes}")
            x1, y1, x2, y2 = b["xyxy"]
            boxes.append(Box(classes.index(b["cls"]), x1 / W, y1 / H, x2 / W, y2 / H))
        if boxes:
            sha = hashlib.sha256(C.resolve(rel, must_exist=True).read_bytes()).hexdigest()
            out.append(Item(f"HC:{rel}", ann.get("collection", "HARD_CASES"), rel, "", sha, f"HC:{rel}", v.get("event_group", ""), "", boxes, Counter()))
    return out


def split_digest(items: list[Item]) -> str:
    """sha256 of the sorted item ids (AI_INFERENCE_ARCHITECTURE §4, held-out integrity)."""
    return hashlib.sha256("\n".join(sorted(i.file_id for i in items)).encode()).hexdigest()


def summarise(items: list[Item], targets: list[str]) -> dict:
    inst = Counter(); imgs = Counter(); dropped = Counter()
    for it in items:
        for b in it.boxes:
            inst[targets[b.cls]] += 1
        for c in {targets[b.cls] for b in it.boxes}:
            imgs[c] += 1
        dropped.update(it.dropped)
    return {"images": len(items), "assets": len({i.asset_id for i in items}), "photo_groups": len({i.photo_group for i in items}),
            "images_with_target": sum(1 for i in items if i.boxes), "images_without_target": sum(1 for i in items if not i.boxes),
            "unlabelled_images": sum(1 for i in items if i.unlabelled),
            "instances": dict(inst), "images_containing": dict(imgs), "dropped_source_instances": dict(dropped),
            "split_digest": split_digest(items)}


# ------------------------------------------------------------------ readiness gate
def readiness(cfg: dict, *, hash_files: bool = False, check_images: bool = True) -> dict:
    """Pre-training readiness gate (Phase 7 §4). Each condition is reported separately."""
    from PIL import Image

    rep: dict = {"experiment_id": cfg.get("experiment_id"), "prep_id": cfg["dataset"]["prep_id"], "conditions": {}, "splits": {}, "problems": []}
    cond = rep["conditions"]
    try:
        mapping = load_mapping(cfg)
    except DataError as e:
        cond["class_mapping_approved"] = {"ok": False, "detail": str(e)}
        rep["ready"] = False
        return rep
    cond["class_mapping_approved"] = {"ok": True, "detail": f"{mapping['mapping_id']} v{mapping['version']}: "
                                      + ", ".join(f"{k[0]}:{k[1]}→{mapping['targets'][v]}" for k, v in sorted(mapping["use"].items()))
                                      + "; dropped " + ", ".join(f"{k[0]}:{d['source_name']} ({d['status']})" for k, d in sorted(mapping["dropped"].items()))}
    per = {}
    for s in C.SPLITS:
        subset = {"train": "all", "valid": cfg["dataset"]["valid_subset"], "test": cfg["dataset"]["test_subset"]}[s]
        items, problems = load_items(cfg, s, mapping, subset=subset, strict=False)
        unreadable, sha_bad, sizes = [], [], Counter()
        if check_images:
            for it in items:
                p = C.resolve(it.rel_path)
                try:
                    with Image.open(p) as im:
                        im.verify()
                    with Image.open(p) as im:
                        sizes[im.size] += 1
                except Exception as e:  # noqa: BLE001
                    unreadable.append(f"{it.file_id}: {type(e).__name__}")
                if hash_files and it.sha256 and hashlib.sha256(p.read_bytes()).hexdigest() != it.sha256:
                    sha_bad.append(it.file_id)
        per[s] = {**summarise(items, mapping["targets"]), "subset": subset, "label_problems": problems[:20], "label_problem_count": len(problems),
                  "unreadable": unreadable[:20], "unreadable_count": len(unreadable), "sha256_mismatch": sha_bad[:20], "sha256_checked": bool(hash_files),
                  "image_sizes": {f"{w}x{h}": n for (w, h), n in sizes.most_common(5)}}
        rep["problems"] += problems[:5] + unreadable[:5] + [f"sha256 mismatch {x}" for x in sha_bad[:5]]
    rep["splits"] = per
    allf = {s: {i.file_id for i in load_items(cfg, s, mapping, strict=False)[0]} for s in C.SPLITS}
    overlap = {f"{a}/{b}": len(allf[a] & allf[b]) for a, b in (("train", "valid"), ("train", "test"), ("valid", "test"))}
    groups = {s: {r["photo_group"] for r in read_split(cfg, s)} for s in C.SPLITS}
    g_overlap = {f"{a}/{b}": len(groups[a] & groups[b]) for a, b in (("train", "valid"), ("train", "test"), ("valid", "test"))}
    cond["files_present"] = {"ok": all("missing" not in p for s in per.values() for p in s["label_problems"]) and all(per[s]["images"] > 0 for s in per), "detail": {s: per[s]["images"] for s in per}}
    cond["files_readable"] = {"ok": check_images and all(per[s]["unreadable_count"] == 0 for s in per), "detail": {s: per[s]["unreadable_count"] for s in per} if check_images else "not checked"}
    cond["files_match_manifest_sha256"] = {"ok": hash_files and all(not per[s]["sha256_mismatch"] for s in per), "detail": "checked" if hash_files else "not checked (use --hash)"}
    cond["annotations_valid"] = {"ok": all(per[s]["label_problem_count"] == 0 for s in per), "detail": {s: per[s]["label_problem_count"] for s in per}}
    cond["no_file_or_group_crosses_splits"] = {"ok": not any(overlap.values()) and not any(g_overlap.values()), "detail": {"files": overlap, "photo_groups": g_overlap}}
    tgt = mapping["targets"]
    enough = {t: {s: per[s]["instances"].get(t, 0) for s in per} for t in tgt}
    cond["each_target_class_present_in_every_split"] = {"ok": all(v > 0 for t in tgt for v in enough[t].values()), "detail": enough}
    rep["mapping"] = {k: v for k, v in mapping.items() if k not in ("use", "dropped")}
    rep["ready"] = all(c["ok"] for k, c in cond.items() if k != "files_match_manifest_sha256" or hash_files)
    return rep


# ------------------------------------------------------------------ dataset + augmentation
def letterbox_params(w: int, h: int, sw: int, sh: int) -> tuple[float, float, float]:
    r = min(sw / w, sh / h)
    return r, (sw - round(w * r)) / 2, (sh - round(h * r)) / 2


class DetectionDataset:
    """Yields (image CHW float32 BGR 0–255 letterboxed, targets [N,5] = cls,cx,cy,w,h in input pixels, meta).

    Augmentations are train-only and transform the boxes with the image (AUG-01/02).
    """

    def __init__(self, items: list[Item], size: tuple[int, int], *, augment: dict | None = None, seed: int = 0, fill: int = 114):
        self.items, (self.sh, self.sw), self.aug, self.fill = items, size, augment, fill
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.items)

    def reseed(self, seed: int):
        self.rng = random.Random(seed)

    def load(self, i: int):
        import numpy as np
        from PIL import Image, ImageOps

        it = self.items[i]
        with Image.open(C.resolve(it.rel_path)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            arr = np.asarray(im, dtype=np.uint8)[:, :, ::-1].copy()   # BGR, the YOLOX convention
        h, w = arr.shape[:2]
        b = np.array([[x.cls, x.x1 * w, x.y1 * h, x.x2 * w, x.y2 * h] for x in it.boxes], dtype=np.float32).reshape(-1, 5)
        return arr, b

    def __getitem__(self, i: int):
        return self._get(i, self.rng)

    def sample(self, i: int, seed: int):
        """Thread-safe and order-independent: the augmentation draws come from a per-sample seed, so batches can be
        loaded in parallel (training prefetch) and still be reproducible."""
        return self._get(i, random.Random(seed))

    def _get(self, i: int, rng: random.Random):
        import numpy as np
        from PIL import Image

        arr, b = self.load(i)
        h, w = arr.shape[:2]
        a = self.aug
        scale, tx, ty, hf, vf = 1.0, 0.0, 0.0, False, False
        if a:
            scale = rng.uniform(*a["scale"])
            tx = rng.uniform(-a["translate"], a["translate"]); ty = rng.uniform(-a["translate"], a["translate"])
            hf = rng.random() < a["hflip"]; vf = rng.random() < a.get("vflip", 0)
        r, px, py = letterbox_params(w, h, self.sw, self.sh)
        r *= scale
        nw, nh = max(1, round(w * r)), max(1, round(h * r))
        px = (self.sw - nw) / 2 + tx * self.sw; py = (self.sh - nh) / 2 + ty * self.sh
        img = np.asarray(Image.fromarray(arr[:, :, ::-1]).resize((nw, nh), Image.BILINEAR), dtype=np.uint8)[:, :, ::-1]
        canvas = np.full((self.sh, self.sw, 3), self.fill, dtype=np.uint8)
        ox, oy = int(round(px)), int(round(py))
        x0, y0 = max(0, ox), max(0, oy); x1, y1 = min(self.sw, ox + nw), min(self.sh, oy + nh)
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1] = img[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        bb = b.copy()
        if len(bb):
            bb[:, [1, 3]] = bb[:, [1, 3]] * r + ox
            bb[:, [2, 4]] = bb[:, [2, 4]] * r + oy
            bb[:, [1, 3]] = bb[:, [1, 3]].clip(0, self.sw); bb[:, [2, 4]] = bb[:, [2, 4]].clip(0, self.sh)
            # keep a box only if ≥ 40 % of its area survives the crop and it stays ≥ MIN_BOX_PX wide/high
            orig_area = ((b[:, 3] - b[:, 1]) * (b[:, 4] - b[:, 2])) * r * r
            area = (bb[:, 3] - bb[:, 1]) * (bb[:, 4] - bb[:, 2])
            keep = (area >= 0.4 * orig_area) & (bb[:, 3] - bb[:, 1] >= MIN_BOX_PX) & (bb[:, 4] - bb[:, 2] >= MIN_BOX_PX)
            bb = bb[keep]
        if hf:
            canvas = canvas[:, ::-1]
            if len(bb):
                bb[:, [1, 3]] = self.sw - bb[:, [3, 1]]
        if vf:
            canvas = canvas[::-1]
            if len(bb):
                bb[:, [2, 4]] = self.sh - bb[:, [4, 2]]
        if a and a.get("hsv"):
            canvas = hsv_jitter(canvas, a["hsv"], rng)
        if a and a.get("photometric"):
            canvas = photometric(canvas, a["photometric"], rng)
        t = np.zeros((len(bb), 5), dtype=np.float32)
        if len(bb):
            t[:, 0] = bb[:, 0]
            t[:, 1] = (bb[:, 1] + bb[:, 3]) / 2; t[:, 2] = (bb[:, 2] + bb[:, 4]) / 2
            t[:, 3] = bb[:, 3] - bb[:, 1]; t[:, 4] = bb[:, 4] - bb[:, 2]
        chw = np.ascontiguousarray(canvas.transpose(2, 0, 1)).astype(np.float32)
        meta = {"index": i, "file_id": self.items[i].file_id, "orig_w": w, "orig_h": h, "ratio": r, "pad_x": ox, "pad_y": oy}
        return chw, t, meta


def photometric(bgr, ph: dict, rng: random.Random):
    """Phase 13 robustness: camera and generator artefacts (blur, sensor noise, JPEG re-compression, greyscale, contrast)
    so the detector holds up on phone photos, drone frames and AI-generated images. Geometry is unchanged."""
    import io as _io

    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter

    im = Image.fromarray(bgr[:, :, ::-1].copy())
    if rng.random() < ph.get("blur", 0):
        im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.6)))
    if rng.random() < ph.get("contrast", 0):
        im = ImageEnhance.Contrast(im).enhance(rng.uniform(0.7, 1.35))
    if rng.random() < ph.get("gray", 0):
        im = im.convert("L").convert("RGB")
    if rng.random() < ph.get("jpeg", 0):
        buf = _io.BytesIO(); im.save(buf, "JPEG", quality=rng.randint(30, 85)); buf.seek(0); im = Image.open(buf).convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    if rng.random() < ph.get("noise", 0):
        a = a + np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(3, 12), a.shape)
    a = np.clip(a, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(a[:, :, ::-1])


def hsv_jitter(bgr, hsv: dict, rng: random.Random):
    """Moderate saturation/value jitter; hue only if configured (bounded to ≤ 0.02 by config)."""
    import numpy as np
    from PIL import Image

    s = 1 + rng.uniform(-hsv.get("s", 0), hsv.get("s", 0)); v = 1 + rng.uniform(-hsv.get("v", 0), hsv.get("v", 0)); hh = rng.uniform(-hsv.get("h", 0), hsv.get("h", 0))
    im = Image.fromarray(bgr[:, :, ::-1].copy()).convert("HSV")
    a = np.asarray(im, dtype=np.float32).copy()
    a[..., 0] = (a[..., 0] + hh * 255) % 256
    a[..., 1] = np.clip(a[..., 1] * s, 0, 255); a[..., 2] = np.clip(a[..., 2] * v, 0, 255)
    rgb = np.asarray(Image.fromarray(a.astype(np.uint8), "HSV").convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


def epoch_indices(items: list[Item], mode: str, rng: random.Random) -> list[int]:
    """'all_files' → every file; 'one_file_per_asset_per_epoch' → one random copy of each Roboflow asset,
    so the triplicated augmented copies of one photograph are not over-weighted and each epoch is cheaper."""
    if mode == "all_files":
        idx = list(range(len(items)))
    else:
        by = defaultdict(list)
        for i, it in enumerate(items):
            by[it.asset_id].append(i)
        idx = [rng.choice(v) for _, v in sorted(by.items())]
    rng.shuffle(idx)
    return idx


def collate(batch, max_labels: int = 120):
    import numpy as np
    import torch

    imgs = torch.from_numpy(np.stack([b[0] for b in batch]))
    t = torch.zeros(len(batch), max_labels, 5)
    for k, b in enumerate(batch):
        n = min(len(b[1]), max_labels)
        if n:
            t[k, :n] = torch.from_numpy(b[1][:n])
    return imgs, t, [b[2] for b in batch]
