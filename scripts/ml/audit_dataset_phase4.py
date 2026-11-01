"""Phase 4 dataset audit: what the training workflow actually sees, per core class, with leakage checks.

    python scripts/ml/audit_dataset_phase4.py --config ml/configs/detection/VT-EXP-010.json --out docs/evidence/phase4/dataset_audit.json

Read-only. Uses the training loader (visentra_ml.detection.data) so the counts are what training/evaluation use.
1. Raw inventory (datasets/manifests/files/*.csv) vs split rows vs quarantine: every exclusion has a recorded reason.
2. Every split row: file present, sha256 matches, image decodes, label parses (loader problems are listed, not hidden).
3. Per class and split: instances, files/assets/photo groups containing the class, relative box-area distribution,
   framing (measured proxy from experiments/audit/scale_framing.json).
4. Cross-split leakage: identical sha256, shared photo group / split group / asset, and 64-bit dHash near-duplicates
   (Hamming <= 4) between train and valid/test.
5. Other image sets (hard cases, clean aircraft, negatives) and the real/synthetic split of everything counted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402

AREA_BINS = [(0, 0.005, "<0.5%"), (0.005, 0.02, "0.5-2%"), (0.02, 0.1, "2-10%"), (0.1, 0.3, "10-30%"), (0.3, 1.01, ">=30%")]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dhash(im: Image.Image) -> int:
    g = np.asarray(im.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-010.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-decode", action="store_true")
    a = ap.parse_args()
    cfg = json.loads((ROOT / a.config).read_text())
    mp = Dt.load_mapping(cfg)
    classes = mp["targets"]
    fram = json.loads((ROOT / "experiments/audit/scale_framing.json").read_text())["splits"]
    out = {"schema": "visiontra.dataset_audit/phase4", "config": a.config, "prep_id": cfg["dataset"]["prep_id"],
           "datasets": cfg["dataset"]["datasets"], "classes": classes, "mapping": {k: mp[k] for k in ("mapping_id", "version", "sha256")},
           "dropped_source_classes": {f"{k[0]}:{k[1]}": v for k, v in mp["dropped"].items()}}

    # 1. raw inventory reconciliation
    raw = {}
    for ds in cfg["dataset"]["datasets"]:
        rows = list(csv.DictReader(open(ROOT / f"datasets/manifests/files/{ds}.csv", encoding="utf-8")))
        raw[ds] = rows
    split_rows = {s: Dt.read_split(cfg, s) for s in ("train", "valid", "test")}
    in_split = {r["file_id"] for rs in split_rows.values() for r in rs}
    quarantine = list(csv.DictReader(open(ROOT / "datasets/quarantine/VT-PREP-001/quarantine.csv", encoding="utf-8")))
    qcols = list(quarantine[0].keys()) if quarantine else []
    qid = {r.get("file_id") for r in quarantine}
    rec = {}
    for ds, rows in raw.items():
        ids = [r.get("file_id") for r in rows]
        img_ids = [i for i, r in zip(ids, rows) if Path(r.get("rel_path", r.get("path", ""))).suffix.lower() in Dt.IMG_EXT or r.get("kind") == "image"]
        not_split = [i for i in img_ids if i not in in_split]
        rec[ds] = {"manifest_rows": len(rows), "image_rows": len(img_ids), "in_splits": sum(1 for i in img_ids if i in in_split),
                   "not_in_splits": len(not_split), "not_in_splits_quarantined": sum(1 for i in not_split if i in qid),
                   "not_in_splits_unexplained": [i for i in not_split if i not in qid][:50],
                   "not_in_splits_unexplained_count": sum(1 for i in not_split if i not in qid)}
    reason_col = next((c for c in qcols if c in ("reason", "reasons", "quarantine_reason")), None)
    out["raw_reconciliation"] = {"per_dataset": rec, "quarantine_rows": len(quarantine), "quarantine_columns": qcols,
                                 "quarantine_reasons": Counter(r.get(reason_col, "?") for r in quarantine).most_common(20) if reason_col else None}

    # 2-3. per split
    per_split, hashes, groups = {}, {}, {}
    for s, rows in split_rows.items():
        items, problems = Dt.load_items(cfg, s, mp, subset="all", strict=False)
        rep_subset = cfg["dataset"]["valid_subset"] if s == "valid" else ("eval_representatives" if s == "test" else "all")
        rep_ids = {i.file_id for i in Dt.load_items(cfg, s, mp, subset=rep_subset, strict=False)[0]}
        byid = {r["file_id"]: r for r in rows}
        missing, bad_sha, undecodable = [], [], []
        h = {}
        stats = {c: {"instances": 0, "files": set(), "assets": set(), "photo_groups": set(), "area_bins": Counter(), "framing_groups": Counter(),
                     "eval_instances": 0, "eval_files": set()} for c in classes}
        dropped = Counter()
        unlabelled = 0
        fr = fram.get(s, {}).get("photo_groups_framing", {})
        for k, it in enumerate(items):
            p = C.resolve(it.rel_path)
            if not p.is_file():
                missing.append(it.file_id); continue
            if sha(p) != byid[it.file_id]["sha256"]:
                bad_sha.append(it.file_id)
            if not a.no_decode:
                try:
                    with Image.open(p) as im:
                        im.load(); h[it.file_id] = dhash(ImageOps.exif_transpose(im))
                except Exception as e:  # noqa: BLE001
                    undecodable.append(f"{it.file_id}: {type(e).__name__}")
            dropped.update(it.dropped)
            unlabelled += it.unlabelled
            g = f"{it.dataset_id}|{it.photo_group}"
            for b in it.boxes:
                c = classes[b.cls]; st = stats[c]
                st["instances"] += 1; st["files"].add(it.file_id); st["assets"].add(it.asset_id); st["photo_groups"].add(g)
                area = (b.x2 - b.x1) * (b.y2 - b.y1)
                st["area_bins"][next(lbl for lo, hi, lbl in AREA_BINS if lo <= area < hi)] += 1
                if it.file_id in rep_ids:
                    st["eval_instances"] += 1; st["eval_files"].add(it.file_id)
            if (k + 1) % 2000 == 0:
                print(f"  {s} {k + 1}/{len(items)}", flush=True)
        for c in classes:
            st = stats[c]
            st["framing_groups"] = Counter(fr.get(g, {}).get("framing", "unmeasured") for g in st["photo_groups"])
        hashes[s] = h
        groups[s] = {"sha": {byid[i.file_id]["sha256"] for i in items}, "photo_group": {f"{i.dataset_id}|{i.photo_group}" for i in items},
                     "split_group": {f"{byid[i.file_id]['dataset_id']}|{byid[i.file_id]['split_group']}" for i in items},
                     "asset": {i.asset_id for i in items}}
        per_split[s] = {"rows": len(rows), "loaded_items": len(items), "loader_problems": len(problems), "loader_problem_examples": problems[:20],
                        "missing_files": missing[:20], "missing_count": len(missing), "sha_mismatch": len(bad_sha), "undecodable": undecodable[:20],
                        "undecodable_count": len(undecodable), "unlabelled_files": unlabelled, "files_without_target": sum(1 for i in items if not i.boxes),
                        "assets": len({i.asset_id for i in items}), "photo_groups": len(groups[s]["photo_group"]),
                        "evaluation_subset": rep_subset, "evaluation_files": len(rep_ids),
                        "dropped_source_instances": dict(dropped),
                        "per_class": {c: {"instances": st["instances"], "files": len(st["files"]), "assets": len(st["assets"]), "photo_groups": len(st["photo_groups"]),
                                          "eval_subset_instances": st["eval_instances"], "eval_subset_files": len(st["eval_files"]),
                                          "relative_area": dict(st["area_bins"]), "framing_of_photo_groups": dict(st["framing_groups"])}
                                      for c, st in stats.items()}}
        print(s, json.dumps({c: per_split[s]["per_class"][c]["instances"] for c in classes}), flush=True)
    out["splits"] = per_split

    # 4. leakage
    leak = {}
    for x, y in (("train", "valid"), ("train", "test"), ("valid", "test")):
        leak[f"{x}-{y}"] = {k: len(groups[x][k] & groups[y][k]) for k in ("sha", "photo_group", "split_group", "asset")}
        if hashes[x] and hashes[y]:
            A = np.array(list(hashes[x].values()), dtype=np.uint64); B = np.array(list(hashes[y].values()), dtype=np.uint64)
            ids_b = list(hashes[y].keys())
            near = 0; examples = []
            for j in range(0, len(B), 256):
                xor = A[:, None] ^ B[None, j:j + 256]
                d = np.unpackbits(xor.view(np.uint8).reshape(xor.shape + (8,)), axis=-1).sum(-1)
                hit = (d <= 4).any(0)
                near += int(hit.sum())
                for jj in np.nonzero(hit)[0][: max(0, 10 - len(examples))]:
                    examples.append(ids_b[j + jj])
            leak[f"{x}-{y}"]["dhash_near_duplicates_in_second_split"] = near
            leak[f"{x}-{y}"]["dhash_examples"] = examples
    out["leakage"] = leak

    # 5. other sets
    hc = json.loads((ROOT / "datasets/hard_cases/VT-HC-001/annotations.json").read_text())
    hc_split = Counter((v.get("split"), v.get("status")) for v in hc["images"].values())
    clean = list(csv.DictReader(open(ROOT / "datasets/hard_cases/VT-CLEAN-001/manifest.csv", encoding="utf-8")))
    negs = {m: Counter((r["split"], r["aircraft"]) for r in csv.DictReader(open(ROOT / f"datasets/negatives/{m}/manifest.csv", encoding="utf-8")))
            for m in ("VT-NEG-001", "VT-NEG-002")}
    out["other_sets"] = {"VT-HC-001": {"images": len(hc["images"]), "by_split_status": {f"{k[0]}|{k[1]}": v for k, v in hc_split.items()},
                                       "role": "train split: 9 weakly reviewed images used with repeat 3 in VT-EXP-010; eval split: development regression only"},
                         "VT-CLEAN-001": {"rows": len(clean), "by_status": dict(Counter(r.get("status") for r in clean)), "role": "false-alarm reference; never training"},
                         "negatives": {m: {f"{k[0]}|{k[1]}": v for k, v in c.items()} for m, c in negs.items()}}
    out["real_vs_synthetic"] = {
        "detector_defect_images": "All VT-DS-001/VT-DS-002 split files are photographs (Roboflow exports with source-side augmentations: rotations/flips/"
                                  "crops of the same photos). No AI-generated defect images exist in the detector corpus.",
        "synthetic_in_corpus": {"VT-NEG-002": "generated documents/charts/interfaces/plain images (CC0) - non-aircraft negatives for the gate and as "
                                              "detector background; not defect imagery"},
        "synthetic_defect_images_available": 0,
        "consequence": "A real-vs-synthetic training comparison cannot be run honestly: there are no verified synthetic aircraft-defect images. "
                       "Real/synthetic metrics are reported only for the hard-case uploads whose provenance is suspected synthetic/edited (flagged, unverified)."}
    Path(ROOT / a.out).write_text(json.dumps(out, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o)), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
