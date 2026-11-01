"""Step 1 — reproducible raw-data inventory and manifest reconciliation (read-only on raw data).

    python scripts/datasets/inventory_dataset.py [--prep VT-PREP-001] [--max-seconds N]

For every file listed in the Phase 3 per-file manifests or present under datasets/raw/<id>/: hash it again
(SHA-256), decode images fully with Pillow (format, mode, size, readability), compute perceptual hashes,
and pair images with YOLO label files. Results are cached per file in
datasets/interim/<PREP>/features_<id>.jsonl so the step can resume after a time budget (exit code 3).

Outputs (datasets/interim/<PREP>/):
  inventory.csv        one row per file (images, labels, metadata) with checksums and warnings
  reconciliation.json  manifest-vs-disk discrepancies and counts
Exit codes: 0 ok · 1 discrepancies found (outputs still written) · 2 configuration/input error · 3 paused
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def walk(base: Path) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), base).replace(os.sep, "/"))
    return sorted(out)


def main() -> int:
    cfg = pc.load_config()
    budget = float(pc.arg("--max-seconds", 0) or 0)
    deadline = time.time() + budget if budget else float("inf")
    idir = pc.out_dir("interim", cfg)
    datasets = pc.acquired_datasets(cfg)
    rows, recon = [], {"run": pc.run_info(cfg), "datasets": {}}
    problems = 0
    for d in datasets:
        ds = d["id"]
        base = pc.RAW / ds
        if not base.is_dir():
            raise pc.PrepError(f"{ds}: raw directory {base.relative_to(pc.ROOT)} not found")
        fm = pc.ROOT / d["acquisition"]["file_manifest"]
        mrows = pc.read_csv(fm)
        names = pc.read_names(base / "data.yaml")
        dup_ids = sorted(k for k, v in Counter(r["file_id"] for r in mrows).items() if v > 1)
        dup_paths = sorted(k for k, v in Counter(r["rel_path"] for r in mrows).items() if v > 1)
        listed = {r["rel_path"].removeprefix(f"datasets/raw/{ds}/"): r for r in mrows}
        on_disk = walk(base)
        disk = set(on_disk)
        missing = sorted(set(listed) - disk)
        unexpected = sorted(disk - set(listed))
        lower = defaultdict(list)
        for r in on_disk:
            lower[r.lower()].append(r)
        case_conflicts = sorted(v for v in lower.values() if len(v) > 1)

        # ---- per-file facts (cached, resumable)
        cpath = idir / f"features_v{pc.FEATURES_VERSION}_{ds}.jsonl"
        cache = {}
        if cpath.exists():
            for line in cpath.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    cache[rec["rel"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue  # partial line from an interrupted run: recomputed below
        todo = [r for r in on_disk if r not in cache]

        def facts(rel):
            p = base / rel
            ext = Path(rel).suffix.lower()
            if ext in pc.IMG_EXT:
                f = pc.inspect_image(p)
            else:
                data = p.read_bytes()
                import hashlib
                f = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                if ext == ".txt":
                    f["text"] = data.decode("utf-8", errors="replace")
            f["rel"] = rel
            return f

        paused = False
        with ThreadPoolExecutor(max_workers=int(os.environ.get("VISIONTRA_READ_THREADS", "16"))) as pool, \
                open(cpath, "a", encoding="utf-8") as out:
            for i in range(0, len(todo), 256):
                if time.time() > deadline:
                    paused = True
                    break
                for f in pool.map(facts, todo[i:i + 256]):
                    cache[f["rel"]] = f
                    out.write(json.dumps(f) + "\n")
        if paused:
            left = sum(1 for r in on_disk if r not in cache)
            print(f"PAUSED: {ds}: {left} of {len(on_disk)} files still to inspect — run again to continue")
            pc.log("inventory_paused", cfg, dataset=ds, remaining=left)
            return 3

        # ---- reconcile and build inventory rows
        labels = {r for r in on_disk if Path(r).suffix.lower() == ".txt" and "/labels/" in "/" + r}
        used_labels = set()
        sha_mismatch, size_mismatch, unreadable, ext_mismatch, no_label, empty_label = [], [], [], [], [], []
        for rel in on_disk:
            f = cache[rel]
            m = listed.get(rel, {})
            ext = Path(rel).suffix.lower()
            parts = rel.split("/")
            split = parts[0] if parts[0] in pc.SPLITS else ""
            kind = "image" if ext in pc.IMG_EXT else "label" if rel in labels else "metadata"
            warn = []
            if m and m.get("sha256") != f["sha256"]:
                sha_mismatch.append(rel)
                warn.append("SHA256_DIFFERS_FROM_PHASE3_MANIFEST")
            if m and str(m.get("bytes")) != str(f["bytes"]):
                size_mismatch.append(rel)
                warn.append("SIZE_DIFFERS_FROM_PHASE3_MANIFEST")
            if not m:
                warn.append("NOT_IN_PHASE3_MANIFEST")
            row = {"file_id": f"{ds}:{rel}", "dataset_id": ds, "rel_path": f"datasets/raw/{ds}/{rel}", "source_split": split,
                   "kind": kind, "original_filename": Path(rel).name, "extension": ext.lstrip("."), "detected_format": "",
                   "bytes": f["bytes"], "width": "", "height": "", "colour_mode": "", "sha256": f["sha256"], "readable": "",
                   "label_path": "", "label_status": "", "n_objects": "", "source_class_ids": "", "source_classes": "",
                   "source_stem": "", "acquisition_id": d["acquisition"]["acquisition_id"], "acquisition_status": d["status"],
                   "phash": "", "dhash": "", "warnings": ""}
            if kind == "image":
                fmt = f.get("detected_format", "")
                row.update(detected_format=fmt, width=f.get("width", ""), height=f.get("height", ""),
                           colour_mode=f.get("mode", ""), readable="yes" if f.get("decoded") else "no",
                           phash=f.get("phash", ""), dhash=f.get("dhash", ""), source_stem=pc.source_stem(Path(rel).stem))
                if not f.get("decoded"):
                    unreadable.append(rel)
                    warn.append(f"UNREADABLE:{f.get('decode_error', '')}")
                expect = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "bmp": "bmp", "webp": "webp", "tif": "tiff", "tiff": "tiff"}[ext.lstrip(".")]
                if fmt and fmt != expect:
                    ext_mismatch.append(rel)
                    warn.append(f"EXTENSION_FORMAT_MISMATCH:{ext}->{fmt}")
                lab = pc.label_rel_for(rel)
                if lab and lab in labels:
                    used_labels.add(lab)
                    txt = cache[lab].get("text", "")
                    row["label_path"] = f"datasets/raw/{ds}/{lab}"
                    ids = []
                    for s in txt.splitlines():
                        try:
                            ids.append(int(float(s.split()[0])))
                        except (ValueError, IndexError):
                            pass
                    row["n_objects"] = len([s for s in txt.splitlines() if s.strip()])
                    row["label_status"] = "EMPTY_LABEL_FILE" if row["n_objects"] == 0 else "LABELLED"
                    if row["n_objects"] == 0:
                        empty_label.append(rel)
                    row["source_class_ids"] = "|".join(str(i) for i in sorted(set(ids)))
                    row["source_classes"] = "|".join(names[i] if 0 <= i < len(names) else f"?{i}" for i in sorted(set(ids)))
                else:
                    row["label_status"] = "NO_LABEL_FILE"
                    no_label.append(rel)
                    warn.append("NO_LABEL_FILE")
            row["warnings"] = "|".join(warn)
            rows.append(row)
        orphans = sorted(labels - used_labels)
        for r in rows:
            if r["dataset_id"] == ds and r["kind"] == "label" and r["rel_path"].removeprefix(f"datasets/raw/{ds}/") in orphans:
                r["warnings"] = "|".join(filter(None, [r["warnings"], "ORPHAN_LABEL_NO_IMAGE"]))
        rec = {
            "phase3_manifest": d["acquisition"]["file_manifest"], "phase3_rows": len(mrows), "files_on_disk": len(on_disk),
            "images_on_disk": sum(1 for r in on_disk if Path(r).suffix.lower() in pc.IMG_EXT), "label_files_on_disk": len(labels),
            "class_names_data_yaml": names,
            "discrepancies": {
                "missing_files": missing, "unexpected_files": unexpected, "duplicate_manifest_file_ids": dup_ids,
                "duplicate_manifest_paths": dup_paths, "sha256_mismatch": sha_mismatch, "size_mismatch": size_mismatch,
                "unreadable_images": unreadable, "extension_format_mismatch": ext_mismatch,
                "label_files_without_image": orphans, "images_without_label_file": no_label,
                "case_insensitive_name_conflicts": case_conflicts,
            },
            "images_with_empty_label_file": len(empty_label),
            "colour_modes": dict(Counter(cache[r].get("mode", "") for r in on_disk if Path(r).suffix.lower() in pc.IMG_EXT)),
            "image_sizes": dict(Counter(f"{cache[r].get('width')}x{cache[r].get('height')}" for r in on_disk if Path(r).suffix.lower() in pc.IMG_EXT)),
            "detected_formats": dict(Counter(cache[r].get("detected_format", "") for r in on_disk if Path(r).suffix.lower() in pc.IMG_EXT)),
        }
        rec["discrepancy_counts"] = {k: len(v) for k, v in rec["discrepancies"].items()}
        # Empty label files are recorded, not treated as discrepancies (their meaning is reviewed in step 5).
        problems += sum(v for k, v in rec["discrepancy_counts"].items())
        recon["datasets"][ds] = rec
    recon["total_discrepancies"] = problems
    fields = list(rows[0].keys())
    pc.write_csv(idir / "inventory.csv", rows, fields)
    pc.write_json(idir / "reconciliation.json", recon)
    pc.log("inventory", cfg, files=len(rows), discrepancies=problems)
    for ds, r in recon["datasets"].items():
        print(f"{ds}: {r['files_on_disk']} files on disk, {r['phase3_rows']} in Phase 3 manifest, "
              f"{r['images_on_disk']} images, discrepancies={r['discrepancy_counts']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
