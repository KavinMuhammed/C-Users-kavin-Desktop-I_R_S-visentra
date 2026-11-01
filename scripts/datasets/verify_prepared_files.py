"""Phase 12 independent re-check of the prepared dataset VT-PREP-001 against the files on this machine.

For every row of datasets/processed/VT-PREP-001/derived_manifest.csv: the image exists, its SHA-256 equals the
manifest, it decodes (PIL verify + load), its YOLO label file exists, and every label line parses with in-range
boxes and a class id known to the dataset's data.yaml. Also re-checks that no file or photo group crosses splits.
Read-only: nothing under datasets/ is written. Output: JSON (default docs/evidence/phase12/dataset_verification.json).

    python scripts/datasets/verify_prepared_files.py [--out PATH] [--no-decode]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]      # overridable with --root (tests use a small synthetic tree)
PREP = "VT-PREP-001"


def names(ds: str) -> list[str]:
    for line in (ROOT / "datasets/raw" / ds / "data.yaml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("names:"):
            return [x.strip().strip("'\"") for x in line.split(":", 1)[1].strip().strip("[]").split(",") if x.strip()]
    return []


def label_path(rel: str) -> Path:
    p = Path(rel); parts = list(p.parts)
    i = len(parts) - 1 - parts[::-1].index("images"); parts[i] = "labels"
    return ROOT / Path(*parts).with_suffix(".txt")


def main() -> int:
    global ROOT
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="docs/evidence/phase12/dataset_verification.json"); ap.add_argument("--no-decode", action="store_true")
    ap.add_argument("--root", help="repository root holding datasets/ (default: this repository)")
    a = ap.parse_args()
    if a.root:
        ROOT = Path(a.root).resolve()
    t0 = time.time()
    rows = list(csv.DictReader(open(ROOT / f"datasets/processed/{PREP}/derived_manifest.csv", newline="", encoding="utf-8")))
    nm = {d: names(d) for d in {r["dataset_id"] for r in rows}}
    c = Counter(); problems = defaultdict(list); box_by_class = Counter()
    split_of_file, split_of_group = {}, defaultdict(set)
    for r in rows:
        p = ROOT / r["rel_path"]
        split_of_file.setdefault(r["file_id"], set()).add(r["assigned_split"])
        split_of_group[r["photo_group"]].add(r["assigned_split"])
        if not p.is_file():
            c["missing_image"] += 1; problems["missing_image"].append(r["file_id"]); continue
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            c["sha256_mismatch"] += 1; problems["sha256_mismatch"].append(r["file_id"])
        if not a.no_decode:
            try:
                with Image.open(p) as im:
                    im.verify()
                with Image.open(p) as im:
                    im.load()
                    if min(im.size) < 1:
                        raise ValueError("zero size")
                c["decoded"] += 1
            except Exception as e:  # noqa: BLE001
                c["corrupt_image"] += 1; problems["corrupt_image"].append(f"{r['file_id']}: {type(e).__name__}")
        lp = label_path(r["rel_path"])
        if not lp.is_file():
            c["missing_label_file"] += 1; problems["missing_label_file"].append(r["file_id"]); continue
        lines = [x.split() for x in lp.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        if not lines:
            c["empty_label_file"] += 1
        for n, f in enumerate(lines, 1):
            try:
                cls = int(f[0]); vals = [float(v) for v in f[1:]]
            except (ValueError, IndexError):
                c["malformed_label_line"] += 1; problems["malformed_label_line"].append(f"{r['file_id']}:{n}"); continue
            if cls < 0 or cls >= len(nm[r["dataset_id"]]):
                c["unknown_class_id"] += 1; problems["unknown_class_id"].append(f"{r['file_id']}:{n}")
            else:
                box_by_class[f"{r['dataset_id']}:{nm[r['dataset_id']][cls]}"] += 1
            if len(vals) == 4:
                cx, cy, w, h = vals; xs, ys = (cx - w / 2, cx + w / 2), (cy - h / 2, cy + h / 2)
            elif len(vals) >= 6 and len(vals) % 2 == 0:
                xs, ys = vals[0::2], vals[1::2]
            else:
                c["malformed_label_line"] += 1; problems["malformed_label_line"].append(f"{r['file_id']}:{n}"); continue
            if min(min(xs), min(ys)) < -1e-6 or max(max(xs), max(ys)) > 1 + 1e-6:
                c["box_out_of_bounds"] += 1; problems["box_out_of_bounds"].append(f"{r['file_id']}:{n}")
            elif max(xs) - min(xs) <= 0 or max(ys) - min(ys) <= 0:
                c["degenerate_box"] += 1; problems["degenerate_box"].append(f"{r['file_id']}:{n}")
    files_across = sum(1 for s in split_of_file.values() if len(s) > 1)
    groups_across = sum(1 for s in split_of_group.values() if len(s) > 1)
    dup = Counter(r["sha256"] for r in rows); exact_dups = sum(n - 1 for n in dup.values() if n > 1)
    out = {"prep_id": PREP, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": len(rows), "seconds": round(time.time() - t0, 1),
           "decode_checked": not a.no_decode, "counts": dict(c), "exact_duplicate_files_by_sha256": exact_dups,
           "files_in_more_than_one_split": files_across, "photo_groups_in_more_than_one_split": groups_across,
           "label_boxes_by_source_class": dict(sorted(box_by_class.items())),
           "examples": {k: v[:10] for k, v in problems.items()},
           "note": "Read-only re-check of files on this machine against the VT-PREP-001 manifest. Unlabelled (empty label file) is not the same as defect-free."}
    dest = ROOT / a.out; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    bad = sum(c[k] for k in ("missing_image", "sha256_mismatch", "corrupt_image", "missing_label_file", "malformed_label_line", "unknown_class_id", "box_out_of_bounds")) + files_across + groups_across
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
