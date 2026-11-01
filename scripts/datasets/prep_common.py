"""Shared helpers for the Phase 4 data-preparation scripts (scripts/datasets/*.py).

Configuration : datasets/config/prep/<PREP-ID>.json      (thresholds, seed, split targets, grouping rules)
                datasets/config/class_mapping.json         (versioned source-class -> TAX-1.0 mapping table)
Inputs        : datasets/manifests/datasets.json, datasets/manifests/files/<VT-DS-id>.csv (Phase 3, read-only)
                datasets/raw/<VT-DS-id>/...                 (immutable; never written)
Outputs       : datasets/interim/<PREP-ID>/, datasets/processed/<PREP-ID>/, datasets/splits/<PREP-ID>/,
                datasets/quarantine/<PREP-ID>/, datasets/reports/<PREP-ID>/, datasets/review/
Log           : datasets/logs/prep.jsonl (append-only; settings and results, never file contents or secrets)

Requires Pillow and NumPy (both already pinned in requirements.txt).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("VISIONTRA_ROOT") or Path(__file__).resolve().parents[2])
DS = ROOT / "datasets"
RAW = DS / "raw"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
RF = re.compile(r"^(?P<stem>.+?)(?:_(?:jpg|jpeg|png|JPG|JPEG|PNG))?\.rf\.(?P<hash>[0-9a-f]{32})$")
SPLITS = ("train", "valid", "test")


class PrepError(SystemExit):
    """Actionable failure: prints the message and exits with code 2."""

    def __init__(self, msg: str):
        print(f"ERROR: {msg}", file=sys.stderr)
        super().__init__(2)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def arg(name: str, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def load_config(prep_id: str | None = None) -> dict:
    prep_id = prep_id or arg("--prep") or os.environ.get("VISIONTRA_PREP_ID") or "VT-PREP-001"
    if not re.fullmatch(r"VT-PREP-\d{3}", prep_id):
        raise PrepError(f"invalid preparation id {prep_id!r} (expected VT-PREP-NNN)")
    p = DS / "config" / "prep" / f"{prep_id}.json"
    if not p.exists():
        raise PrepError(f"configuration not found: {p.relative_to(ROOT)}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if cfg.get("prep_id") != prep_id:
        raise PrepError(f"{p.name}: prep_id field {cfg.get('prep_id')!r} does not match file name")
    cfg["_config_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    cfg["_config_path"] = p.relative_to(ROOT).as_posix()
    return cfg


def out_dir(kind: str, cfg: dict) -> Path:
    """Destination directory for derived outputs. Refuses anything under datasets/raw/."""
    assert kind in ("interim", "processed", "splits", "quarantine", "reports"), kind
    d = DS / kind / cfg["prep_id"]
    guard(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def guard(path: Path) -> None:
    """Raw data is immutable: no derived output may be written inside datasets/raw/."""
    rp = os.path.normpath(os.path.abspath(path))
    raw = os.path.normpath(os.path.abspath(RAW))
    if rp == raw or rp.startswith(raw + os.sep):
        raise PrepError(f"refusing to write inside raw data: {path}")


def write_text(path: Path, text: str) -> None:
    guard(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def write_json(path: Path, obj) -> None:
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0].keys()) if rows else ["empty"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("|".join(map(str, v)) if isinstance(v, (list, tuple, set)) else v) for k, v in r.items()})
    write_text(path, buf.getvalue())


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise PrepError(f"missing input {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} — run the previous step first")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path):
    if not path.exists():
        raise PrepError(f"missing input {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} — run the previous step first")
    return json.loads(path.read_text(encoding="utf-8"))


def log(event: str, cfg: dict, **fields) -> None:
    rec = {"at": now(), "event": event, "prep_id": cfg["prep_id"], "config_sha256": cfg["_config_sha256"],
           "script": Path(sys.argv[0]).name, **fields}
    p = DS / "logs" / "prep.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def run_info(cfg: dict) -> dict:
    return {"prep_id": cfg["prep_id"], "config": cfg["_config_path"], "config_sha256": cfg["_config_sha256"],
            "generated_by": Path(sys.argv[0]).name, "python": platform.python_version()}


def master_manifest() -> dict:
    return read_json(DS / "manifests" / "datasets.json")


def acquired_datasets(cfg: dict) -> list[dict]:
    m = master_manifest()
    byid = {d["id"]: d for d in m["datasets"]}
    out = []
    for i in cfg["datasets"]:
        if i not in byid:
            raise PrepError(f"{i} is configured but not in datasets/manifests/datasets.json")
        d = byid[i]
        if d["status"] not in ("INTEGRITY_VERIFIED", "DOWNLOADED", "PARTIAL"):
            raise PrepError(f"{i} has status {d['status']}; only acquired datasets can be prepared")
        out.append(d)
    return out


def source_stem(filename_stem: str) -> str:
    m = RF.match(filename_stem)
    return m.group("stem") if m else filename_stem


def label_rel_for(image_rel: str) -> str | None:
    parts = image_rel.split("/")
    if len(parts) >= 2 and parts[-2] == "images":
        return "/".join(parts[:-2] + ["labels", Path(parts[-1]).stem + ".txt"])
    return None


def read_names(yaml_path: Path) -> list[str]:
    if not yaml_path.exists():
        return []
    txt = yaml_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^names:\s*\[(.*?)\]", txt, re.M | re.S)
    if m:
        return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"^names:\s*\n((?:\s*-\s*.+\n?)+)", txt, re.M)
    return [x.strip()[1:].strip().strip("'\"") for x in m.group(1).splitlines() if x.strip()] if m else []


# ---------------------------------------------------------------- YOLO parsing and validation
def parse_yolo(text: str, nc: int, width: int | None, height: int | None, tol: float, min_px: float) -> dict:
    """Validate one YOLO label file. Returns objects plus issues (each with code and severity).
    Severities: INVALID (excluded from derived training data), REVIEW (human review), RECOVERABLE (a
    deterministic correction is possible and is proposed, never applied to the raw file)."""
    objs, issues = [], []
    seen = {}
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        tok = s.split()
        try:
            c_f = float(tok[0])
            vals = [float(x) for x in tok[1:]]
        except ValueError:
            issues.append({"line": ln, "code": "NOT_NUMERIC", "severity": "INVALID"})
            continue
        if not c_f.is_integer():
            issues.append({"line": ln, "code": "CLASS_NOT_INTEGER", "severity": "INVALID"})
            continue
        c = int(c_f)
        if c < 0 or c >= nc:
            issues.append({"line": ln, "code": "CLASS_ID_OUT_OF_RANGE", "severity": "INVALID", "class_id": c})
            continue
        if not all(math.isfinite(v) for v in vals):
            issues.append({"line": ln, "code": "NON_FINITE", "severity": "INVALID"})
            continue
        if len(vals) == 4:
            x, y, w, h = vals
            kind = "box"
            if w <= 0 or h <= 0:
                issues.append({"line": ln, "code": "NON_POSITIVE_SIZE", "severity": "INVALID"})
                continue
            x0, y0, x1, y1 = x - w / 2, y - h / 2, x + w / 2, y + h / 2
        elif len(vals) >= 6 and len(vals) % 2 == 0:
            kind = "polygon"
            xs, ys = vals[0::2], vals[1::2]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                issues.append({"line": ln, "code": "DEGENERATE_POLYGON", "severity": "INVALID"})
                continue
            x, y, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
        else:
            issues.append({"line": ln, "code": "WRONG_FIELD_COUNT", "severity": "INVALID", "fields": len(tok)})
            continue
        if x0 < -tol or y0 < -tol or x1 > 1 + tol or y1 > 1 + tol:
            # Not clipped automatically: clipping changes geometry and is proposed for review only.
            issues.append({"line": ln, "code": "OUTSIDE_IMAGE", "severity": "REVIEW",
                           "extent": [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)]})
        if width and height and (w * width < min_px or h * height < min_px):
            issues.append({"line": ln, "code": "TINY_BOX", "severity": "REVIEW", "px": [round(w * width, 2), round(h * height, 2)]})
        key = (c, round(x, 6), round(y, 6), round(w, 6), round(h, 6))
        if key in seen:
            issues.append({"line": ln, "code": "DUPLICATE_OBJECT", "severity": "RECOVERABLE", "same_as_line": seen[key]})
        seen[key] = ln
        objs.append({"line": ln, "class_id": c, "kind": kind, "x": x, "y": y, "w": w, "h": h})
    return {"objects": objs, "issues": issues}


def canonical_label(text: str | None) -> str | None:
    """Order-independent normalised label content, used to compare annotations of identical images."""
    if text is None:
        return None
    lines = []
    for s in text.splitlines():
        if s.strip():
            try:
                lines.append(" ".join([str(int(float(s.split()[0])))] + [f"{float(v):.6f}" for v in s.split()[1:]]))
            except ValueError:
                lines.append("RAW:" + s.strip())
    return "\n".join(sorted(lines))


# ---------------------------------------------------------------- perceptual hashing
_DCT = {}


def _dct_matrix(n: int):
    import numpy as np
    if n not in _DCT:
        k = np.arange(n)
        m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * math.sqrt(2 / n)
        m[0] /= math.sqrt(2)
        _DCT[n] = m
    return _DCT[n]


def phash_bits(gray32) -> int:
    """64-bit DCT perceptual hash of a 32x32 float array (low 8x8 frequencies vs their median, DC excluded)."""
    import numpy as np
    d = _dct_matrix(32)
    coef = (d @ gray32 @ d.T)[:8, :8].flatten()
    med = np.median(coef[1:])
    bits = 0
    for v in coef:
        bits = (bits << 1) | int(v > med)
    return bits


FEATURES_VERSION = 4  # bump when hashing changes; caches are keyed by it


def _dhash(gray_img) -> int:
    import numpy as np
    from PIL import Image
    d9 = np.asarray(gray_img.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = 0
    for v in (d9[:, 1:] > d9[:, :-1]).flatten():
        bits = (bits << 1) | int(v)
    return bits


def image_hashes(img) -> dict:
    """pHash and dHash of the image and of its 7 other dihedral transforms (rotations/flips).
    Variant order k: rot90^(k//2), then a left-right flip when k is odd (same order for both hashes)."""
    import numpy as np
    from PIL import Image
    g = img.convert("L")
    a = np.asarray(g.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    pv, dv = [], []
    for k in range(4):
        r = np.rot90(a, k)
        pv.append(phash_bits(r))
        pv.append(phash_bits(np.fliplr(r)))
        gr = g.rotate(90 * k, expand=True)  # PIL rotates counter-clockwise, like np.rot90
        dv.append(_dhash(gr))
        dv.append(_dhash(gr.transpose(Image.Transpose.FLIP_LEFT_RIGHT)))
    t16 = np.asarray(g.resize((16, 16), Image.Resampling.LANCZOS), dtype=np.uint8)
    # central half of the image: excludes the black corner wedges that rotation augmentation adds, which
    # otherwise dominate the hashes of low-texture images (observed false matches, VT-PREP-001)
    W, H = g.size
    c = np.asarray(g.crop((W // 4, H // 4, W - W // 4, H - H // 4)).resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    cv = []
    for k in range(4):
        r = np.rot90(c, k)
        cv.append(phash_bits(r))
        cv.append(phash_bits(np.fliplr(r)))
    return {"phash_center_dihedral": [f"{v:016x}" for v in cv], "phash": f"{pv[0]:016x}", "phash_dihedral": [f"{v:016x}" for v in pv],
            "dhash": f"{dv[0]:016x}", "dhash_dihedral": [f"{v:016x}" for v in dv], "thumb16": t16.tobytes().hex()}


def thumb_variant(hexstr: str, k: int):
    """16x16 grey thumbnail (float array) under dihedral variant k (same convention as image_hashes)."""
    import numpy as np
    a = np.frombuffer(bytes.fromhex(hexstr), dtype=np.uint8).reshape(16, 16).astype(np.float64)
    r = np.rot90(a, k // 2)
    return np.fliplr(r) if k % 2 else r


def ncc(a, b) -> float:
    """Normalised cross-correlation of two equal-size arrays (1 = identical up to brightness/contrast)."""
    import numpy as np
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def inspect_image(path: Path) -> dict:
    """Read the whole file, hash it, decode it fully with Pillow. Never raises for bad media."""
    from PIL import Image, UnidentifiedImageError
    out = {"bytes": None, "sha256": None, "decoded": False, "decode_error": "", "detected_format": "", "mode": "",
           "width": "", "height": "", "phash": "", "phash_dihedral": "", "dhash": "", "dhash_dihedral": "", "thumb16": "", "phash_center_dihedral": ""}
    try:
        data = path.read_bytes()
    except OSError as e:
        out["decode_error"] = f"READ_ERROR:{type(e).__name__}"
        return out
    out["bytes"] = len(data)
    out["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        with Image.open(io.BytesIO(data)) as im:
            out["detected_format"] = (im.format or "").lower()
            out["width"], out["height"] = im.size
            out["mode"] = im.mode
            im.load()  # full decode: truncated or corrupt data raises here
            out.update(image_hashes(im))
            out["decoded"] = True
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        out["decode_error"] = f"{type(e).__name__}:{str(e)[:80]}"
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.p[rb] = ra  # smallest id is the root: deterministic


def stable_hash(*parts) -> int:
    return int(hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:16], 16)


# ---------------------------------------------------------------- shared loaders for later steps
def load_features(cfg: dict) -> dict:
    """file_id -> cached facts from inventory_dataset.py (hashes, image facts, label text)."""
    idir = DS / "interim" / cfg["prep_id"]
    out = {}
    for ds in cfg["datasets"]:
        p = idir / f"features_v{FEATURES_VERSION}_{ds}.jsonl"
        if not p.exists():
            raise PrepError(f"missing {p.relative_to(ROOT)} — run inventory_dataset.py first")
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[f"{ds}:{r['rel']}"] = r
    return out


def load_inventory(cfg: dict) -> list[dict]:
    return read_csv(DS / "interim" / cfg["prep_id"] / "inventory.csv")


def asset_id(row: dict) -> str:
    """Source-asset identity: dataset + Roboflow source stem (augmented exports share the stem)."""
    return f"{row['dataset_id']}/{row['source_stem']}"


def match_rule(rules: list[dict], dataset: str, stem: str, key: str) -> str | None:
    for r in rules:
        if r["dataset"] == dataset:
            m = re.match(r["pattern"], stem)
            if m:
                v = r[key]
                for i, g in enumerate(m.groups(), 1):
                    v = v.replace("{%d}" % i, g or "")
                return f"{dataset}/{v}"
    return None
