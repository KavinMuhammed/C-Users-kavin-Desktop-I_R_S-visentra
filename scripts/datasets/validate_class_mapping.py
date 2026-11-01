"""Step 4b — validate the versioned class-mapping table against the real source classes.

    python scripts/datasets/validate_class_mapping.py [--prep VT-PREP-001] [--spot-check 24]

Checks datasets/config/class_mapping.json: every class in every dataset's data.yaml appears exactly once with the
same id and name; targets exist in the taxonomy; statuses are known; only PROVISIONAL_ACCEPTED entries are applied;
UNRESOLVED / NOT_MAPPED entries have no applied target. Counts affected instances, images and source assets from
annotation_status.csv. Writes a deterministic spot-check sample (one object per source asset) for every mapped or
candidate class to datasets/review/class_mapping_spot_check.csv (existing decisions kept) and a tile list for
generate_contact_sheets.py.
Outputs: datasets/reports/<PREP>/class_mapping_report.json
Exit codes: 0 ok · 1 mapping problems · 2 input error
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402

STATUSES = {"PROVISIONAL_ACCEPTED", "UNRESOLVED", "NOT_MAPPED"}
SPOT_STATES = {"PENDING_REVIEW", "CONSISTENT", "INCONSISTENT", "UNCLEAR"}


def load_mapping(cfg: dict) -> dict:
    return pc.read_json(pc.ROOT / cfg["class_mapping"])


def applied_map(mapping: dict) -> dict:
    """(dataset, source_name) -> target name, for PROVISIONAL_ACCEPTED entries only."""
    return {(e["dataset"], e["source_name"]): e["target"] for e in mapping["entries"] if e["status"] == "PROVISIONAL_ACCEPTED"}


def main() -> int:
    cfg = pc.load_config()
    mp = load_mapping(cfg)
    problems = []
    targets = {t["name"] for t in mp["target_classes"]}
    if mp.get("taxonomy") != cfg["taxonomy"]:
        problems.append(f"mapping taxonomy {mp.get('taxonomy')} != config taxonomy {cfg['taxonomy']}")
    seen = Counter((e["dataset"], e["source_class_id"]) for e in mp["entries"])
    problems += [f"{k}: listed {v} times" for k, v in seen.items() if v > 1]
    for e in mp["entries"]:
        tag = f"{e['dataset']}:{e['source_class_id']}:{e['source_name']}"
        if e["status"] not in STATUSES:
            problems.append(f"{tag}: unknown status {e['status']}")
        if e["status"] == "PROVISIONAL_ACCEPTED" and e.get("target") not in targets:
            problems.append(f"{tag}: accepted mapping to unknown target {e.get('target')!r}")
        if e["status"] == "NOT_MAPPED" and e.get("target"):
            problems.append(f"{tag}: NOT_MAPPED entry must not name a target")
        if e["status"] != "NOT_MAPPED" and not e.get("rationale"):
            problems.append(f"{tag}: rationale required")
    for ds in cfg["datasets"]:
        names = pc.read_names(pc.RAW / ds / "data.yaml")
        listed = {e["source_class_id"]: e["source_name"] for e in mp["entries"] if e["dataset"] == ds}
        for i, n in enumerate(names):
            if i not in listed:
                problems.append(f"{ds}: source class {i} {n!r} is not in the mapping table (classes must not be dropped silently)")
            elif listed[i] != n:
                problems.append(f"{ds}: class id {i} is {n!r} in data.yaml but {listed[i]!r} in the mapping table")
        for i in listed:
            if i >= len(names):
                problems.append(f"{ds}: mapping lists class id {i} that data.yaml does not define")

    # affected counts from validated annotations
    status = pc.read_csv(pc.DS / "interim" / cfg["prep_id"] / "annotation_status.csv")
    inv = {r["file_id"]: r for r in pc.load_inventory(cfg)}
    feats = pc.load_features(cfg)
    inst, imgs, assets = Counter(), Counter(), defaultdict(set)
    objects = defaultdict(list)  # (ds, class) -> [(asset, file_id, box)]
    names_of = {ds: pc.read_names(pc.RAW / ds / "data.yaml") for ds in cfg["datasets"]}  # read once (slow shared folders)
    for s in status:
        if s["annotation_status"] not in ("VALID", "VALID_WITH_WARNINGS", "REQUIRES_REVIEW"):
            continue
        r = inv[s["file_id"]]
        ds = r["dataset_id"]
        names = names_of[ds]
        txt = feats[f"{ds}:{r['label_path'].split('/', 3)[-1]}"]["text"]
        res = pc.parse_yolo(txt, len(names), None, None, 1.0, 0)
        for c in {names[o["class_id"]] for o in res["objects"]}:
            imgs[(ds, c)] += 1
            assets[(ds, c)].add(pc.asset_id(r))
        for o in res["objects"]:
            c = names[o["class_id"]]
            inst[(ds, c)] += 1
            objects[(ds, c)].append((pc.asset_id(r), r["file_id"], f"{o['x']:.6f} {o['y']:.6f} {o['w']:.6f} {o['h']:.6f}"))
    entries = []
    for e in mp["entries"]:
        k = (e["dataset"], e["source_name"])
        entries.append({**{x: e.get(x) for x in ("dataset", "source_class_id", "source_name", "target", "status")},
                        "instances": inst[k], "images": imgs[k], "source_assets": len(assets[k]),
                        "applied_in_derived_view": e["status"] == "PROVISIONAL_ACCEPTED"})
    # spot-check sample: one object per source asset, deterministic order
    n = int(pc.arg("--spot-check", 24))
    rows, tiles = [], []
    for e in mp["entries"]:
        if e["status"] == "NOT_MAPPED":
            continue
        k = (e["dataset"], e["source_name"])
        per_asset = {}
        for a, f, b in sorted(objects[k], key=lambda t: pc.stable_hash(cfg["split"]["seed"], t[1], t[2])):
            per_asset.setdefault(a, (f, b))
        picks = sorted(per_asset.items(), key=lambda t: pc.stable_hash(cfg["split"]["seed"], t[0]))[:n]
        for i, (a, (f, b)) in enumerate(picks, 1):
            rid = f"MAP-{e['dataset']}-{e['source_name']}-{i:02d}"
            rows.append({"review_id": rid, "dataset": e["dataset"], "source_class": e["source_name"], "candidate_target": e["target"],
                         "mapping_status": e["status"], "file_id": f, "box": b, "review_status": "PENDING_REVIEW", "assigned_label": "",
                         "reviewer": "", "reviewed_at": "", "review_method": "", "review_notes": "", "evidence": "", "uncertainty": ""})
            tiles.append({"file_id": f, "caption": f"{e['source_name']} #{i}", "row": "", "box": b})
    path = pc.DS / "review" / "class_mapping_spot_check.csv"
    if path.exists():
        old = {r["review_id"]: r for r in pc.read_csv(path)}
        for r in rows:
            o = old.get(r["review_id"])
            if o and o["file_id"] == r["file_id"] and o["box"] == r["box"] and o["review_status"] != "PENDING_REVIEW":
                for c in ("review_status", "assigned_label", "reviewer", "reviewed_at", "review_method", "review_notes", "evidence", "uncertainty"):
                    r[c] = o[c]
    for r in rows:
        if r["review_status"] not in SPOT_STATES:
            problems.append(f"{path.name}: {r['review_id']} has unknown review_status {r['review_status']}")
    pc.write_csv(path, rows)
    pc.write_csv(pc.out_dir("interim", cfg) / "review_lists" / "class_mapping_spot_check.csv", tiles, ["file_id", "caption", "row", "box"])
    spot = Counter((r["source_class"], r["review_status"]) for r in rows)
    report = {"run": pc.run_info(cfg), "mapping_file": cfg["class_mapping"], "mapping_id": mp["mapping_id"], "mapping_version": mp["version"],
              "problems": problems, "entries": entries, "spot_check": {f"{a} | {b}": v for (a, b), v in sorted(spot.items())},
              "applied_targets": sorted({e["target"] for e in mp["entries"] if e["status"] == "PROVISIONAL_ACCEPTED"}),
              "target_classes_without_applied_source": sorted(targets - {e["target"] for e in mp["entries"] if e["status"] == "PROVISIONAL_ACCEPTED"})}
    pc.write_json(pc.out_dir("reports", cfg) / "class_mapping_report.json", report)
    pc.log("class_mapping", cfg, problems=len(problems), applied=report["applied_targets"])
    for e in entries:
        print(f"{e['dataset']} {e['source_class_id']} {e['source_name']:<14} -> {str(e['target']):<10} {e['status']:<21} "
              f"instances={e['instances']} images={e['images']} assets={e['source_assets']}")
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
