"""Step 2 — deterministic exact-duplicate detection by SHA-256 of image bytes (read-only on raw data).

    python scripts/datasets/detect_exact_duplicates.py [--prep VT-PREP-001]

Groups images whose bytes are identical, whatever their names or folders. For each group the YOLO
annotations are compared (order-independent); identical bytes with different annotations are recorded as
ANNOTATION_CONFLICT and every member is quarantined later (no label is chosen silently).
Canonical copy (for derived data only; nothing is deleted): the member that appears first by
(dataset order in the config, file_id) — deterministic and documented, not a quality judgement.

Outputs: datasets/interim/<PREP>/exact_duplicates.json and exact_duplicate_members.csv
Exit codes: 0 ok (groups may exist) · 2 input error
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def main() -> int:
    cfg = pc.load_config()
    inv = [r for r in pc.load_inventory(cfg) if r["kind"] == "image"]
    feats = pc.load_features(cfg)
    order = {d: i for i, d in enumerate(cfg["datasets"])}
    by_sha = defaultdict(list)
    for r in inv:
        by_sha[r["sha256"]].append(r)
    groups, members = [], []
    for sha, rs in sorted(by_sha.items()):
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda r: (order[r["dataset_id"]], r["file_id"]))
        labels = {}
        for r in rs:
            lab = r["label_path"].split("/", 3)[-1] if r["label_path"] else None
            lab_id = f"{r['dataset_id']}:{lab}" if lab else None
            txt = feats.get(lab_id, {}).get("text") if lab_id else None
            labels[r["file_id"]] = pc.canonical_label(txt)
        distinct = set(labels.values())
        conflict = len(distinct) > 1
        gid = f"EXD-{len(groups) + 1:05d}"
        groups.append({
            "group_id": gid, "sha256": sha, "bytes": int(rs[0]["bytes"]), "size": len(rs),
            "datasets": sorted({r["dataset_id"] for r in rs}), "file_ids": [r["file_id"] for r in rs],
            "source_splits": [r["source_split"] for r in rs],
            "annotations": "CONFLICT" if conflict else "IDENTICAL",
            "status": "ANNOTATION_CONFLICT_REQUIRES_REVIEW" if conflict else "DUPLICATE_ANNOTATIONS_AGREE",
            "canonical_file_id": None if conflict else rs[0]["file_id"],
            "canonical_rule": None if conflict else "first by (configured dataset order, file_id); no copy is deleted",
        })
        for r in rs:
            members.append({"group_id": gid, "file_id": r["file_id"], "dataset_id": r["dataset_id"], "rel_path": r["rel_path"],
                            "sha256": sha, "bytes": r["bytes"], "source_split": r["source_split"],
                            "label_digest": pc.stable_hash(labels[r["file_id"]]) if labels[r["file_id"]] is not None else "",
                            "canonical": "yes" if groups[-1]["canonical_file_id"] == r["file_id"] else "no"})
    idir = pc.out_dir("interim", cfg)
    summary = {"run": pc.run_info(cfg), "images_checked": len(inv), "unique_sha256": len(by_sha),
               "duplicate_groups": len(groups), "files_in_duplicate_groups": sum(g["size"] for g in groups),
               "redundant_copies": sum(g["size"] - 1 for g in groups),
               "cross_dataset_groups": sum(1 for g in groups if len(g["datasets"]) > 1),
               "annotation_conflict_groups": sum(1 for g in groups if g["annotations"] == "CONFLICT"),
               "groups_spanning_source_splits": sum(1 for g in groups if len(set(g["source_splits"])) > 1),
               "groups": groups}
    pc.write_json(idir / "exact_duplicates.json", summary)
    pc.write_csv(idir / "exact_duplicate_members.csv", members,
                 ["group_id", "file_id", "dataset_id", "rel_path", "sha256", "bytes", "source_split", "label_digest", "canonical"])
    pc.log("exact_duplicates", cfg, images=len(inv), groups=len(groups), conflicts=summary["annotation_conflict_groups"])
    print(f"{len(inv)} images, {len(by_sha)} distinct SHA-256 values, {len(groups)} exact-duplicate groups "
          f"({summary['annotation_conflict_groups']} with conflicting annotations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
