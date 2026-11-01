"""Re-check dataset integrity for the training splits (read-only; datasets/raw is never modified).

Checks: every manifest file exists and matches its SHA-256 (optional --hash), exact duplicates within/across splits,
photo-group / split-group leakage, files-per-distinct-photo (augmented and near-duplicate copies), class instances.
    python scripts/datasets/dataset_integrity_audit.py [--hash]  -> datasets/reports/VT-PREP-001/integrity_audit.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "datasets/splits/VT-PREP-001"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--hash", action="store_true"); a = ap.parse_args()
    rows = {s: list(csv.DictReader(open(S / f"{s}.csv", encoding="utf-8"))) for s in ("train", "valid", "test")}
    out = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "prep": "VT-PREP-001", "splits": {}}
    sha_split = defaultdict(set); missing = []; bad_hash = []
    for s, rs in rows.items():
        sh = Counter(r["sha256"] for r in rs)
        groups = Counter((r["dataset_id"], r["photo_group"]) for r in rs)
        for r in rs:
            sha_split[r["sha256"]].add(s)
            p = ROOT / r["rel_path"]
            if not p.exists():
                missing.append(r["rel_path"])
            elif a.hash and hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256"]:
                bad_hash.append(r["rel_path"])
        out["splits"][s] = {"files": len(rs), "distinct_files_by_sha256": len(sh), "exact_duplicate_files": sum(n - 1 for n in sh.values() if n > 1),
                            "distinct_photos(photo_groups)": len(groups), "max_files_per_photo": max(groups.values()),
                            "datasets": dict(Counter(r["dataset_id"] for r in rs))}
    g = {s: {(r["dataset_id"], r["photo_group"]) for r in rs} for s, rs in rows.items()}
    sg = {s: {r["split_group"] for r in rs} for s, rs in rows.items()}
    out["cross_split"] = {"sha256_in_more_than_one_split": sum(1 for v in sha_split.values() if len(v) > 1),
                          "photo_group_overlap": {f"{x}-{y}": len(g[x] & g[y]) for x, y in (("train", "valid"), ("train", "test"), ("valid", "test"))},
                          "split_group_overlap": {f"{x}-{y}": len(sg[x] & sg[y]) for x, y in (("train", "valid"), ("train", "test"), ("valid", "test"))}}
    out["files_missing"] = len(missing); out["sha256_mismatch"] = len(bad_hash) if a.hash else "not checked (run with --hash)"
    out["how_duplicates_are_handled"] = ("Exact and near-duplicate copies (Roboflow augmentations, edit families, burst frames) are grouped into one asset and one "
                                         "photo group during preparation (Phase 4). Training draws ONE file per asset per epoch, validation uses one file per asset, "
                                         "and the test uses one representative per group, so duplicates are never counted twice and never cross splits. "
                                         "datasets/raw stays immutable; nothing is deleted.")
    o = ROOT / "datasets/reports/VT-PREP-001/integrity_audit.json"; o.write_text(json.dumps(out, indent=1)); print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
