"""Step 8 — dataset quality report from the generated manifests (read-only).

    python scripts/datasets/generate_dataset_quality_report.py [--prep VT-PREP-001] [--check]

Every number is recomputed from the Phase 4 manifests (inventory, annotation status, derived manifest, splits,
review files); nothing is typed by hand. --check recomputes and compares with the stored report and with
split_summary.json / annotation_validation.json, exiting 1 on any disagreement.
Output: datasets/reports/<PREP>/data_quality_report.json

Counting units (never mixed):
  files            image files on disk (Roboflow augmented copies count separately)
  source assets    dataset + Roboflow source stem (Phase 3's "unique source images")
  photo groups     source assets merged with exact duplicates, near-identical/transformed candidates and capture
                   families: the best available estimate of distinct photographs / video frames
  instances        annotation objects (label lines)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def build(cfg: dict) -> dict:
    idir = pc.DS / "interim" / cfg["prep_id"]
    inv = [r for r in pc.load_inventory(cfg) if r["kind"] == "image"]
    ann = {r["file_id"]: r for r in pc.read_csv(idir / "annotation_status.csv")}
    der = pc.read_csv(pc.DS / "processed" / cfg["prep_id"] / "derived_manifest.csv")
    exd = pc.read_json(idir / "exact_duplicates.json")
    ndg = pc.read_json(idir / "near_duplicate_groups.json")
    def opt(name):
        p = pc.DS / "review" / name
        return pc.read_csv(p) if p.exists() else []
    sample = opt("near_duplicate_sample.csv")
    unl = {ds: opt(f"unlabelled_{ds}.csv") for ds in cfg["datasets"]}
    dom = {ds: opt(f"domain_screen_{ds}.csv") for ds in cfg.get("review", {}).get("domain_screen_datasets", [])}
    spot = opt("class_mapping_spot_check.csv")
    neg = pc.read_csv(pc.DS / "processed" / cfg["prep_id"] / "negative_inventory.csv")
    warn = pc.read_csv(pc.DS / "reports" / cfg["prep_id"] / "cross_split_similarity_warnings.csv")
    mapping = pc.read_json(pc.ROOT / cfg["class_mapping"])
    feats = pc.load_features(cfg)

    def per(rows_inv, rows_der):
        ids = {r["file_id"] for r in rows_inv}
        insts, insts_ret = Counter(), Counter()
        insts_split, insts_rep = defaultdict(Counter), defaultdict(Counter)
        split_of = {r["file_id"]: r["assigned_split"] for r in rows_der}
        rep_ids = {r["file_id"] for r in rows_der if r["eval_representative"] == "yes"}
        retained_ids = {r["file_id"] for r in rows_der if r["inclusion_status"] == "RETAINED"}
        a_cfg = cfg["annotation"]
        names_of = {ds: pc.read_names(pc.RAW / ds / "data.yaml") for ds in cfg["datasets"]}  # read once (slow shared folders)
        for r in rows_inv:
            if r["label_path"]:
                ds = r["dataset_id"]
                names = names_of[ds]
                txt = feats[f"{ds}:{r['label_path'].split('/', 3)[-1]}"]["text"]
                w = int(r["width"]) if r["width"] else None
                h = int(r["height"]) if r["height"] else None
                for o in pc.parse_yolo(txt, len(names), w, h, a_cfg["box_tolerance"], a_cfg["min_box_px"])["objects"]:
                    insts[names[o["class_id"]]] += 1
                    if r["file_id"] in retained_ids:
                        insts_ret[names[o["class_id"]]] += 1
                        insts_split[split_of[r["file_id"]]][names[o["class_id"]]] += 1
                        if r["file_id"] in rep_ids:
                            insts_rep[split_of[r["file_id"]]][names[o["class_id"]]] += 1
        cls_files, cls_assets, cls_pg = Counter(), defaultdict(set), defaultdict(set)
        for r in rows_der:
            for c in r["source_classes"].split("|"):
                if c:
                    cls_files[c] += 1
                    cls_assets[c].add(r["asset_id"])
                    cls_pg[c].add(r["photo_group"])
        mapped_pg = defaultdict(set)
        cand_pg = defaultdict(set)
        for r in rows_der:
            if r["inclusion_status"] != "RETAINED":
                continue
            for c in r["mapped_classes"].split("|"):
                if c:
                    mapped_pg[c].add(r["photo_group"])
            for c in r["unresolved_candidate_classes"].split("|"):
                if c:
                    cand_pg[c].add(r["photo_group"])
        st = Counter(ann[f]["annotation_status"] for f in ids)
        split = {}
        for s in pc.SPLITS:
            sel = [r for r in rows_der if r["assigned_split"] == s and r["inclusion_status"] == "RETAINED"]
            pcls = defaultdict(set)
            for r in sel:
                for c in r["source_classes"].split("|"):
                    if c:
                        pcls[c].add(r["photo_group"])
            split[s] = {"files": len(sel), "photo_groups": len({r["photo_group"] for r in sel}),
                        "eval_representatives": sum(1 for r in sel if r["eval_representative"] == "yes"),
                        "instances_by_source_class": dict(sorted(insts_split[s].items())),
                        "instances_in_eval_representatives": dict(sorted(insts_rep[s].items())),
                        "photo_groups_containing_source_class": {c: len(v) for c, v in sorted(pcls.items())}}
        return {
            "image_files": len(rows_inv), "readable": sum(1 for r in rows_inv if r["readable"] == "yes"),
            "unreadable": sum(1 for r in rows_inv if r["readable"] != "yes"),
            "source_assets": len({pc.asset_id(r) for r in rows_inv}), "photo_groups": len({r["photo_group"] for r in rows_der}),
            "exact_duplicate_files": sum(1 for r in rows_der if r["exact_duplicate_group"]),
            "assets_in_near_duplicate_candidate_groups": sum(1 for g in ndg["candidate_groups"] for a in g["assets"]
                                                             if any(a.startswith(d["dataset_id"] + "/") for d in rows_der[:1])),
            "images_without_label_file": st.get("UNLABELLED", 0) - sum(1 for r in rows_inv if r["label_status"] == "EMPTY_LABEL_FILE"),
            "images_with_empty_label_file": sum(1 for r in rows_inv if r["label_status"] == "EMPTY_LABEL_FILE"),
            "annotation_status": dict(st), "invalid_annotation_images": st.get("INVALID", 0),
            "annotation_requires_review_images": st.get("REQUIRES_REVIEW", 0),
            "instances_by_source_class": dict(insts), "instances_in_retained_images": dict(insts_ret),
            "files_containing_source_class": dict(cls_files),
            "source_assets_containing_source_class": {c: len(v) for c, v in cls_assets.items()},
            "photo_groups_containing_source_class": {c: len(v) for c, v in cls_pg.items()},
            "retained_photo_groups_by_applied_target_class": {c: len(v) for c, v in mapped_pg.items()},
            "retained_photo_groups_by_unresolved_candidate": {c: len(v) for c, v in cand_pg.items()},
            "capture_families_with_several_assets": sum(1 for n in Counter(
                fam for fam, _ in {(r["capture_family"], r["asset_id"]) for r in rows_der if r["capture_family"]}).values() if n > 1),
            "video_sequences": len({r["video_sequence"] for r in rows_der if r["video_sequence"]}),
            "sessions": len({r["session"] for r in rows_der if r["session"]}), "bursts": len({r["burst"] for r in rows_der if r["burst"]}),
            "aircraft": sorted({r["aircraft"] for r in rows_der}),
            "inclusion": dict(Counter(r["inclusion_status"] for r in rows_der)),
            "inclusion_reasons": dict(Counter(x for r in rows_der for x in r["inclusion_reasons"].split("|") if x)),
            "flags": dict(Counter(x for r in rows_der for x in r["flags"].split("|") if x)),
            "splits": split,
        }
    out = {"run": pc.run_info(cfg), "datasets": {}, "combined": None}
    for ds in cfg["datasets"]:
        ri = [r for r in inv if r["dataset_id"] == ds]
        rd = [r for r in der if r["dataset_id"] == ds]
        out["datasets"][ds] = per(ri, rd)
    out["combined"] = per(inv, der)
    out["combined"]["assets_in_near_duplicate_candidate_groups"] = ndg["assets_in_groups"]
    out["exact_duplicates"] = {k: exd[k] for k in ("duplicate_groups", "files_in_duplicate_groups", "cross_dataset_groups", "annotation_conflict_groups")}
    out["near_duplicates"] = {"pair_categories": ndg["pair_categories"], "candidate_groups": ndg["groups"],
                              "reviewed_sample": dict(Counter(f"{r['category']} -> {r['review_status']}" for r in sample))}
    out["reviews"] = {**{f"unlabelled_{ds}": dict(Counter(r["review_status"] for r in v)) for ds, v in unl.items() if v},
                      **{f"domain_screen_{ds}": dict(Counter(r["review_status"] for r in v)) for ds, v in dom.items()},
                      "class_mapping_spot_check": dict(Counter(f"{r['source_class']}: {r['review_status']}" for r in spot))}
    out["negatives"] = dict(Counter(r["category"] for r in neg))
    out["negatives"]["VERIFIED_NORMAL"] = out["negatives"].get("VERIFIED_NORMAL", 0)
    bins = Counter()
    for w in warn:
        d = int(w["phash_at_variant"])
        bins["<=6" if d <= 6 else "8" if d <= 8 else "10"] += 1
    out["cross_split_similarity_warnings_by_phash_distance"] = dict(bins)
    out["class_mapping"] = [{k: e.get(k) for k in ("dataset", "source_name", "target", "status")} for e in mapping["entries"]]
    out["licence_and_provenance_issues"] = cfg.get("licence_and_provenance_issues", [])
    return out


def main() -> int:
    cfg = pc.load_config()
    rep = build(cfg)
    path = pc.out_dir("reports", cfg) / "data_quality_report.json"
    if "--check" in sys.argv:
        problems = []
        old = pc.read_json(path)
        a = dict(old)
        b = json.loads(json.dumps(rep))
        a.pop("run", None)
        b.pop("run", None)
        if a != b:
            problems.append("stored data_quality_report.json differs from a fresh recomputation")
        ss = pc.read_json(pc.DS / "splits" / cfg["prep_id"] / "split_summary.json")
        for ds in cfg["datasets"]:
            for s in pc.SPLITS:
                x, y = rep["datasets"][ds]["splits"][s], ss["splits"][ds][s]
                if (x["files"], x["photo_groups"], x["eval_representatives"]) != (y["files"], y["photo_groups"], y["eval_representatives"]):
                    problems.append(f"{ds}/{s}: report and split_summary disagree")
        av = pc.read_json(pc.DS / "reports" / cfg["prep_id"] / "annotation_validation.json")
        for ds in cfg["datasets"]:
            if rep["datasets"][ds]["instances_by_source_class"] != av["datasets"][ds]["instances_by_source_class"]:
                problems.append(f"{ds}: instance counts disagree with annotation_validation.json")
            if rep["datasets"][ds]["annotation_status"] != av["datasets"][ds]["image_annotation_status"]:
                problems.append(f"{ds}: annotation status counts disagree with annotation_validation.json")
        for p in problems:
            print("PROBLEM:", p)
        print(f"report check: {len(problems)} problem(s)")
        return 1 if problems else 0
    pc.write_json(path, rep)
    pc.log("quality_report", cfg, path=str(path.relative_to(pc.ROOT)))
    c = rep["combined"]
    print(f"combined: {c['image_files']} image files, {c['source_assets']} source assets, {c['photo_groups']} photo groups; "
          f"inclusion {c['inclusion']}; negatives {rep['negatives']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
