"""Build the master and per-file dataset manifests from what is actually on disk.

    python scripts/datasets/build_manifest.py [--max-seconds N] [--trust-cache]
        resumable: per-file hashes are cached in datasets/manifests/cache/; --trust-cache reuses cached
        entries without re-reading file metadata (for slow network folders; then run
        validate_manifest.py --full, which re-hashes every file)

Inputs : datasets/metadata/candidates.json (curated research facts)
         datasets/raw/<DATASET-ID>/...        (acquired files)
         datasets/logs/acquisition.jsonl      (acquisition results)
Outputs: datasets/manifests/datasets.json      (master manifest, schema visiontra.dataset_manifest/1)
         datasets/manifests/files/<ID>.csv     (per-file manifest)
         datasets/reports/class_coverage.json  (measured class coverage)
Standard library only. Counts come from the files themselves, never from stated values.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("VISIONTRA_ROOT") or Path(__file__).resolve().parents[2])  # override for tests
DS = ROOT / "datasets"
IMG_EXT = {".jpg", ".jpeg", ".png"}
RF = re.compile(r"^(?P<stem>.+?)(?:_(?:jpg|jpeg|png|JPG|JPEG|PNG))?\.rf\.(?P<hash>[0-9a-f]{32})$")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def image_size(p: Path) -> tuple[int | None, int | None, str]:
    """Width, height and detected format from the file header (JPEG SOF / PNG IHDR)."""
    with open(p, "rb") as f:
        head = f.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return w, h, "png"
        if head[:2] != b"\xff\xd8":
            return None, None, "unknown"
        f.seek(2)
        while True:
            b = f.read(1)
            while b and b != b"\xff":
                b = f.read(1)
            while b == b"\xff":
                b = f.read(1)
            if not b:
                return None, None, "jpeg-truncated"
            marker = b[0]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            seg = f.read(2)
            if len(seg) < 2:
                return None, None, "jpeg-truncated"
            length = struct.unpack(">H", seg)[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                d = f.read(5)
                h, w = struct.unpack(">HH", d[1:5])
                return w, h, "jpeg"
            f.seek(length - 2, 1)


def jpeg_complete(p: Path) -> bool:
    with open(p, "rb") as f:
        f.seek(-2, 2)
        return f.read(2) == b"\xff\xd9"


def read_names(yaml_path: Path) -> list[str]:
    """Class names from a Roboflow data.yaml ('names: [a, b]' or a '- name' list)."""
    if not yaml_path.exists():
        return []
    txt = yaml_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^names:\s*\[(.*?)\]", txt, re.M | re.S)
    if m:
        return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"^names:\s*\n((?:\s*-\s*.+\n?)+)", txt, re.M)
    return [x.strip()[1:].strip().strip("'\"") for x in m.group(1).splitlines() if x.strip()] if m else []


def latest_extract_results() -> dict:
    res = {}
    lp = DS / "logs" / "acquisition.jsonl"
    if lp.exists():
        for line in lp.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("event") == "extract_result":
                res[r["dataset"]] = r
            if r.get("event") == "archive_sha256":
                res["_archive_sha256"] = r["sha256"]
            if r.get("event") in ("archive_downloaded", "archive_reused"):
                res["_archive_at"] = r["at"]
    return res


class Budget(Exception):
    pass


def file_facts(ds_id: str, deadline: float) -> dict:
    """sha256 + image header facts for every file, cached in datasets/manifests/cache/<id>.json keyed by
    (size, mtime). Threaded; raises Budget when the time budget is exhausted (cache is saved)."""
    from concurrent.futures import ThreadPoolExecutor
    base = DS / "raw" / ds_id
    cdir = DS / "manifests" / "cache"
    cdir.mkdir(parents=True, exist_ok=True)
    cpath = cdir / f"{ds_id}.json"
    cache = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    facts = {}
    todo = []
    for dirpath, _, fnames in os.walk(base):  # directory listing only — no per-file stat here
        for fn in fnames:
            fp = os.path.join(dirpath, fn)
            todo.append((os.path.relpath(fp, base).replace(os.sep, "/"), fp))

    trust = "--trust-cache" in sys.argv
    if trust:  # raw data is immutable; validate_manifest.py --full re-hashes every file independently
        for rel, _ in todo:
            if rel in cache:
                facts[rel] = cache[rel]
        todo = [t for t in todo if t[0] not in facts]
    else:
        todo.sort(key=lambda t: t[0] in cache)  # uncached files first

    def compute(item):
        rel, fp = item
        st = os.stat(fp)  # per-file metadata is slow on network/synced folders: done in worker threads
        key = f"{st.st_size}:{int(st.st_mtime)}"
        c = cache.get(rel)
        if c and c.get("key") == key:
            return rel, c
        p = Path(fp)
        data = p.read_bytes()
        f = {"key": key, "bytes": st.st_size, "sha256": hashlib.sha256(data).hexdigest()}
        if p.suffix.lower() == ".txt" and "/labels/" in "/" + rel and len(data) < 1_000_000:
            f["text"] = data.decode("utf-8", errors="replace")  # YOLO label content, small
        if p.suffix.lower() in IMG_EXT:
            w, h, fmt = image_size(p)
            f.update(width=w, height=h, format=fmt, complete=bool(w) and (fmt != "jpeg" or jpeg_complete(p)))
        return rel, f
    out_of_time = False
    with ThreadPoolExecutor(max_workers=int(os.environ.get("VISIONTRA_READ_THREADS", "16"))) as pool:
        for i in range(0, len(todo), 256):
            if time.time() > deadline:
                out_of_time = True
                break
            for rel, f in pool.map(compute, todo[i:i + 256]):
                facts[rel] = cache[rel] = f
    cpath.write_text(json.dumps(cache), encoding="utf-8")
    if out_of_time:
        raise Budget(f"{ds_id}: {len(todo) - len(facts)} of {len(todo)} files still to process")
    return facts


def scan(ds_id: str, names: list[str], facts: dict):
    base = DS / "raw" / ds_id
    rows, cls_inst, cls_imgs, cls_src = [], Counter(), defaultdict(set), defaultdict(set)
    split_imgs, split_src, src_splits = Counter(), defaultdict(set), defaultdict(set)
    empty_label, missing_label, bad_lines, invalid_img, orphan_labels = 0, 0, 0, 0, 0
    box_out_of_range = 0
    labels = {r: base / r for r in facts if r.endswith(".txt") and "/labels/" in "/" + r}
    used_labels = set()
    for rel in sorted(facts):
        p = base / rel
        ext = p.suffix.lower()
        parts = rel.split("/")
        split = parts[0] if parts[0] in ("train", "valid", "val", "test") else ""
        kind = "image" if ext in IMG_EXT else "label" if rel in labels else "metadata"
        fx = facts[rel]
        row = {"file_id": f"{ds_id}:{rel}", "dataset_id": ds_id, "rel_path": f"datasets/raw/{ds_id}/{rel}", "split": split, "kind": kind,
               "original_filename": p.name, "format": ext.lstrip("."), "bytes": fx["bytes"], "width": "", "height": "", "sha256": fx["sha256"],
               "label_path": "", "n_objects": "", "classes": "", "source_stem": "", "status": "INTEGRITY_VERIFIED"}
        if kind == "image":
            w, h, fmt, ok = fx.get("width"), fx.get("height"), fx.get("format", "unknown"), fx.get("complete", False)
            row.update(width=w or "", height=h or "", format=fmt)
            if not ok:
                invalid_img += 1
                row["status"] = "CORRUPT"
            stem = p.stem
            m = RF.match(stem)
            src = m.group("stem") if m else stem
            row["source_stem"] = src
            split_imgs[split] += 1
            split_src[split].add(src)
            src_splits[src].add(split)
            lab_rel = "/".join(parts[:-2] + ["labels", p.stem + ".txt"]) if len(parts) >= 2 and parts[-2] == "images" else None
            if lab_rel and lab_rel in labels:
                used_labels.add(lab_rel)
                row["label_path"] = f"datasets/raw/{ds_id}/{lab_rel}"
                present, n = set(), 0
                txt = facts[lab_rel]["text"] if "text" in facts[lab_rel] else labels[lab_rel].read_text(encoding="utf-8", errors="replace")
                for line in txt.splitlines():
                    if not line.strip():
                        continue
                    f = line.split()
                    try:
                        c = int(f[0]); vals = [float(x) for x in f[1:]]
                    except (ValueError, IndexError):
                        bad_lines += 1
                        continue
                    if len(vals) < 4 or c < 0 or (names and c >= len(names)):
                        bad_lines += 1
                        continue
                    if len(vals) == 4:
                        x, y, bw, bh = vals
                        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1.0001 and 0 < bh <= 1.0001):
                            box_out_of_range += 1
                    name = names[c] if names else str(c)
                    cls_inst[name] += 1
                    present.add(name)
                    n += 1
                for name in present:
                    cls_imgs[name].add(rel)
                    cls_src[name].add(src)
                if n == 0:
                    empty_label += 1
                row.update(n_objects=n, classes="|".join(sorted(present)))
            else:
                missing_label += 1
        rows.append(row)
    orphan_labels = len(set(labels) - used_labels)
    leaked = sorted(s for s, sp in src_splits.items() if len(sp) > 1)
    stats = {
        "files": len(rows), "bytes": sum(r["bytes"] for r in rows),
        "images": sum(split_imgs.values()), "images_by_split": dict(split_imgs),
        "unique_source_images": len(src_splits), "unique_source_images_by_split": {k: len(v) for k, v in split_src.items()},
        "label_files": len(labels), "images_without_label_file": missing_label, "images_with_empty_label_file": empty_label,
        "orphan_label_files": orphan_labels, "malformed_label_lines": bad_lines, "boxes_out_of_range": box_out_of_range,
        "invalid_images": invalid_img, "source_images_in_more_than_one_split": len(leaked), "leak_examples": leaked[:5],
        "class_names": names, "instances_by_class": dict(cls_inst),
        "images_containing_class": {k: len(v) for k, v in cls_imgs.items()},
        "unique_source_images_containing_class": {k: len(v) for k, v in cls_src.items()},
        "image_sizes": dict(Counter(f"{r['width']}x{r['height']}" for r in rows if r["kind"] == "image").most_common(5)),
    }
    return rows, stats


def main() -> int:
    budget = float(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 0
    deadline = time.time() + budget if budget else float("inf")
    cand = json.loads((DS / "metadata" / "candidates.json").read_text(encoding="utf-8"))
    acq_res = latest_extract_results()
    out, coverage = [], {}
    (DS / "manifests" / "files").mkdir(parents=True, exist_ok=True)
    for c in cand["candidates"]:
        rec = {k: c.get(k) for k in ("id", "name", "publisher", "landing_url", "download_url", "download_route", "version", "domain", "media", "task", "annotation_format", "source_classes", "class_mapping")}
        rec["licence"] = c.get("licence")
        rec["intended_use_compatible"] = c.get("licence", {}).get("status") in ("VERIFIED", "VERIFIED_WITH_NOTE") and c.get("licence", {}).get("commercial_use", "").startswith("permitted")
        rec["relevance"] = c.get("relevance")
        rec["decision"] = c.get("decision")
        rec["stated_counts"] = c.get("stated_counts")
        rec["known_limitations"] = c.get("known_limitations", [])
        base = DS / "raw" / c["id"]
        r = acq_res.get(c["id"])
        if base.exists() and any(base.rglob("*")):
            names = read_names(base / "data.yaml")
            try:
                facts = file_facts(c["id"], deadline)
            except Budget as e:
                print(f"PAUSED (time budget): {e} — run again to continue")
                return 3
            rows, st = scan(c["id"], names, facts)
            fpath = DS / "manifests" / "files" / f"{c['id']}.csv"
            with open(fpath, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            complete = bool(r and r.get("complete"))
            rec["status"] = "INTEGRITY_VERIFIED" if complete and st["invalid_images"] == 0 else "PARTIAL" if r else "DOWNLOADED"
            rec["acquisition"] = {"acquisition_id": next((a["id"] for a in cand.get("acquisitions", []) if any(e["dataset"] == c["id"] for e in a["extract"])), None), "acquired_at": acq_res.get("_archive_at"),
                                  "archive_sha256": acq_res.get("_archive_sha256"), "extract_result": r, "location": f"datasets/raw/{c['id']}/",
                                  "file_manifest": f"datasets/manifests/files/{c['id']}.csv"}
            rec["measured"] = st
            coverage[c["id"]] = {"class_mapping": c.get("class_mapping"), **{k: st[k] for k in ("images", "unique_source_images", "images_by_split", "unique_source_images_by_split", "instances_by_class", "images_containing_class", "unique_source_images_containing_class", "images_with_empty_label_file", "source_images_in_more_than_one_split")}}
        else:
            rec["status"] = c.get("status")
            rec["acquisition"] = None
            rec["measured"] = None
        out.append(rec)
    manifest = {"schema": "visiontra.dataset_manifest/1", "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generator": "scripts/datasets/build_manifest.py", "datasets": out}
    (DS / "manifests" / "datasets.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (DS / "reports").mkdir(parents=True, exist_ok=True)
    (DS / "reports" / "class_coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    for d in out:
        m = d["measured"]
        print(f"{d['id']:<10} {d['status']:<20} " + (f"images={m['images']} unique_sources={m['unique_source_images']} instances={m['instances_by_class']}" if m else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
