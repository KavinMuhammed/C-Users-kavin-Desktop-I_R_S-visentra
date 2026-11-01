"""Step 7 — validate the grouped splits for leakage, provenance and coverage (read-only).

    python scripts/datasets/validate_splits.py [--prep VT-PREP-001] [--full]

Errors (exit 1):
  - an image missing from the derived manifest, assigned to more than one split, or listed in a split file but not RETAINED
  - a split group, photo group, source asset, capture family, video sequence, burst or held-out session spread over splits
  - identical image bytes (SHA-256) in more than one split
  - a NEAR_IDENTICAL / TRANSFORMED / EXACT candidate pair across splits
  - derived rows whose SHA-256 differs from the inventory (with --full: from a fresh hash of the raw file)
  - split files that do not reproduce the recorded assignment digest
Warnings (reported, not errors):
  - SIMILAR_UNRESOLVED pairs across splits (written to cross_split_similarity_warnings.csv for review)
  - images with no grouping evidence beyond their own source asset
  - a source class present in a dataset but absent from one of its splits
  - single-aircraft datasets (no aircraft-independent evaluation possible)
Output: datasets/reports/<PREP>/split_validation.json, cross_split_similarity_warnings.csv
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def main() -> int:
    cfg = pc.load_config()
    full = "--full" in sys.argv
    sdir = pc.DS / "splits" / cfg["prep_id"]
    derived = pc.read_csv(pc.DS / "processed" / cfg["prep_id"] / "derived_manifest.csv")
    inv = {r["file_id"]: r for r in pc.load_inventory(cfg) if r["kind"] == "image"}
    summary = pc.read_json(sdir / "split_summary.json")
    errors, warnings = [], []

    def err(m):
        errors.append(m)

    # 1. coverage and uniqueness
    ids = Counter(r["file_id"] for r in derived)
    for f, n in ids.items():
        if n > 1:
            err(f"{f}: {n} rows in derived manifest")
    for f in inv:
        if f not in ids:
            err(f"{f}: image in inventory but not in derived manifest")
    for r in derived:
        if r["file_id"] not in inv:
            err(f"{r['file_id']}: derived row without inventory record (provenance broken)")
        elif inv[r["file_id"]]["sha256"] != r["sha256"]:
            err(f"{r['file_id']}: sha256 differs from inventory")
        if r["assigned_split"] not in pc.SPLITS:
            err(f"{r['file_id']}: invalid assigned_split {r['assigned_split']!r}")
    by_id = {r["file_id"]: r for r in derived}
    listed = defaultdict(list)
    for s in pc.SPLITS:
        for x in pc.read_csv(sdir / f"{s}.csv"):
            listed[x["file_id"]].append(s)
            d = by_id.get(x["file_id"])
            if not d:
                err(f"{s}.csv: {x['file_id']} not in derived manifest")
            elif d["inclusion_status"] != "RETAINED":
                err(f"{s}.csv: {x['file_id']} is {d['inclusion_status']} but listed for use")
            elif d["assigned_split"] != s:
                err(f"{s}.csv: {x['file_id']} assigned to {d['assigned_split']}")
    for f, ss in listed.items():
        if len(ss) > 1:
            err(f"{f}: listed in {ss}")
    retained = [r for r in derived if r["inclusion_status"] == "RETAINED"]
    missing = [r["file_id"] for r in retained if r["file_id"] not in listed]
    if missing:
        err(f"{len(missing)} RETAINED images missing from split files, e.g. {missing[:3]}")
    digest = hashlib.sha256("\n".join(f"{r['file_id']}\t{r['assigned_split']}\t{r['inclusion_status']}"
                                      for r in sorted(derived, key=lambda r: r["file_id"])).encode()).hexdigest()
    if digest != summary.get("assignment_sha256"):
        err("assignment digest does not match split_summary.json (manifests edited or out of date)")

    # 2. grouping integrity (all images, quarantined included, so a later approval cannot leak)
    held = {v["session"] for v in cfg["split"].get("session_holdout", {}).values()}
    integrity = {}
    for col in ("split_group", "photo_group", "asset_id", "capture_family", "video_sequence", "burst", "session", "exact_duplicate_group"):
        spread = defaultdict(set)
        for r in derived:
            if r.get(col):
                spread[r[col]].add(r["assigned_split"])
        bad = sorted(k for k, v in spread.items() if len(v) > 1)
        if col == "session":
            bad_held = [k for k in bad if k in held]
            integrity[col] = {"held_out_sessions_spread": len(bad_held), "other_sessions_spread_over_train_valid": len(bad) - len(bad_held)}
            for k in bad_held:
                err(f"held-out session {k} spread over {sorted(spread[k])}")
            for k in bad:
                if k not in held and spread[k] & {v["split"] for v in cfg["split"].get("session_holdout", {}).values()}:
                    err(f"session {k} reaches the held-out split {sorted(spread[k])}")
            continue
        integrity[col] = len(bad)
        for k in bad[:20]:
            err(f"{col} {k} spread over {sorted(spread[k])}")
    # 3. exact bytes across splits
    sha_split = defaultdict(set)
    for r in derived:
        sha_split[r["sha256"]].add(r["assigned_split"])
    cross_sha = [h for h, v in sha_split.items() if len(v) > 1]
    for h in cross_sha[:20]:
        err(f"identical bytes sha256={h[:12]} in {sorted(sha_split[h])}")
    # 4. near-duplicate pairs across splits
    split_of = {r["file_id"]: r["assigned_split"] for r in derived}
    pairs = pc.read_csv(pc.DS / "interim" / cfg["prep_id"] / "near_duplicate_pairs.csv")
    cross = Counter()
    warn_rows = []
    for p in pairs:
        a, b = split_of.get(p["file_a"]), split_of.get(p["file_b"])
        if a != b:
            cross[p["category"]] += 1
            if p["category"] != "SIMILAR_UNRESOLVED":
                err(f"{p['category']} pair {p['pair_id']} across {a}/{b}")
            else:
                warn_rows.append({"pair_id": p["pair_id"], "file_a": p["file_a"], "split_a": a, "file_b": p["file_b"], "split_b": b,
                                  "phash_at_variant": p["phash_at_variant"], "dhash_at_variant": p["dhash_at_variant"],
                                  "center_phash_at_variant": p.get("center_phash_at_variant", ""), "relation_hint": p["relation_hint"]})
    rdir = pc.out_dir("reports", cfg)
    pc.write_csv(rdir / "cross_split_similarity_warnings.csv", warn_rows,
                 ["pair_id", "file_a", "split_a", "file_b", "split_b", "phash_at_variant", "dhash_at_variant", "center_phash_at_variant", "relation_hint"])
    if warn_rows:
        warnings.append(f"{len(warn_rows)} SIMILAR_UNRESOLVED pairs cross splits (review list written); by hint: "
                        f"{dict(Counter(w['relation_hint'] for w in warn_rows))}")
    # 5. unknown grouping
    unknown = Counter(r["dataset_id"] for r in derived if not (r["capture_family"] or r["video_sequence"] or r["session"] or r["exact_duplicate_group"])
                      and int(r["near_duplicate_links"] or 0) == 0)
    for ds, n in unknown.items():
        warnings.append(f"{ds}: {n} images have no grouping evidence beyond their own source asset (relationships to other images unknown)")
    # 6. class coverage per split
    coverage = {}
    for ds in cfg["datasets"]:
        cls = defaultdict(lambda: Counter())
        for r in retained:
            if r["dataset_id"] == ds:
                for c in r["source_classes"].split("|"):
                    if c:
                        cls[c][r["assigned_split"]] += 1
        coverage[ds] = {c: dict(v) for c, v in cls.items()}
        for c, v in cls.items():
            for s in pc.SPLITS:
                if not v.get(s):
                    warnings.append(f"{ds}: source class {c!r} has no images in {s}")
    # 7. aircraft independence
    for ds, a in cfg["grouping"].get("aircraft", {}).items():
        if a.get("aircraft"):
            warnings.append(f"{ds}: all images come from one aircraft ({a['aircraft']}); its valid/test splits measure same-aircraft performance only")
        else:
            warnings.append(f"{ds}: aircraft identity unknown; splits cannot be checked for aircraft independence")
    # 8. raw files (optional full re-hash)
    rehashed = 0
    if full:
        for r in derived:
            if hashlib.sha256((pc.ROOT / r["rel_path"]).read_bytes()).hexdigest() != r["sha256"]:
                err(f"{r['file_id']}: raw file hash changed")
            rehashed += 1
    report = {"run": pc.run_info(cfg), "errors": len(errors), "error_examples": errors[:50], "warnings": warnings,
              "images": len(derived), "retained": len(retained), "assignment_sha256": digest, "grouping_spread_counts": integrity,
              "identical_bytes_across_splits": len(cross_sha), "candidate_pairs_across_splits": dict(cross),
              "class_coverage_images_by_split": coverage, "raw_files_rehashed": rehashed}
    pc.write_json(rdir / "split_validation.json", report)
    pc.log("validate_splits", cfg, errors=len(errors), warnings=len(warnings), rehashed=rehashed)
    print(f"{len(derived)} images, {len(retained)} retained; errors={len(errors)}; candidate pairs across splits: {dict(cross)}; raw re-hashed: {rehashed}")
    for w in warnings:
        print("WARNING:", w)
    for e in errors[:50]:
        print("ERROR:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
