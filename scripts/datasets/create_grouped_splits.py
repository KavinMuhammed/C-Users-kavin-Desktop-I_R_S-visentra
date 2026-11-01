"""Step 6 — source grouping, derived (cleaned) manifest, quarantine list and leakage-resistant grouped splits.

    python scripts/datasets/create_grouped_splits.py [--prep VT-PREP-001]

Nothing is copied or moved: every output is a manifest that references the immutable raw files.

Grouping hierarchy (strongest available evidence first; see docs/DATA_SPLIT_STRATEGY.md):
  1 source asset       dataset + Roboflow source stem (augmented copies of one uploaded image)
  2 exact duplicates   identical SHA-256
  3 near duplicates    NEAR_IDENTICAL / TRANSFORMED_COPY candidate pairs (conservatively treated as related)
  4 capture family     config family_rules (e.g. NNN_k_JPG = edits of photo NNN)
  5 video sequence     config sequence_rules (inferred from frame-extraction file names)
  6 session / burst    config session_rules (one day) and burst_rules (photos < max_gap_seconds apart). A session listed
                       in split.session_holdout is kept whole and assigned to that split; other sessions are grouped by
                       burst and may only go to train/valid, so the held-out session stays session-independent.
  7 aircraft           config grouping.aircraft (applied only when a dataset has more than one known aircraft)
"photo group" = 1-4 (an estimate of distinct photographs / frames); "split group" = 1-6. Splits are assigned
per split group, deterministically (seeded order + greedy deficit balancing on photo groups and source classes),
separately for each dataset. Individual files are never moved to balance counts.

Inclusion: EXCLUDED (unreadable / invalid annotation), QUARANTINED (unresolved: unlabelled images, annotation
conflicts, suspected non-aircraft content) or RETAINED (possibly with warning flags). Quarantined images keep
their group's split so that a later approval cannot leak them into another split.
Outputs: datasets/processed/<PREP>/derived_manifest.csv, negative_inventory.csv
         datasets/quarantine/<PREP>/quarantine.csv
         datasets/splits/<PREP>/{train,valid,test}.csv, groups.csv, split_summary.json
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402

ORDERED = list(pc.SPLITS)


def review_rows(name: str) -> list[dict]:
    p = pc.DS / "review" / name
    return pc.read_csv(p) if p.exists() else []


def main() -> int:
    cfg = pc.load_config()
    g = cfg["grouping"]
    sp = cfg["split"]
    targets = sp["targets"]
    if abs(sum(targets.values()) - 1) > 1e-9 or set(targets) != set(ORDERED):
        raise pc.PrepError("split.targets must define train/valid/test fractions summing to 1")
    seed = sp["seed"]
    inv = sorted((r for r in pc.load_inventory(cfg) if r["kind"] == "image"), key=lambda r: r["file_id"])
    idir = pc.DS / "interim" / cfg["prep_id"]
    ann = {r["file_id"]: r for r in pc.read_csv(idir / "annotation_status.csv")}
    exd = pc.read_json(idir / "exact_duplicates.json")
    pairs = pc.read_csv(idir / "near_duplicate_pairs.csv")
    mapping = pc.read_json(pc.ROOT / cfg["class_mapping"])
    applied = {(e["dataset"], e["source_name"]): e["target"] for e in mapping["entries"] if e["status"] == "PROVISIONAL_ACCEPTED"}
    candidate = {(e["dataset"], e["source_name"]): e["target"] for e in mapping["entries"] if e["status"] == "UNRESOLVED"}

    # ---- bursts (photos close in time within one session)
    holdout = sp.get("session_holdout", {})
    burst_of = {}
    for rule in g.get("burst_rules", []):
        import re
        stamps = defaultdict(list)
        for r in inv:
            if r["dataset_id"] == rule["dataset"]:
                m = re.match(rule["pattern"], r["source_stem"])
                if m:
                    stamps[pc.match_rule(g.get("session_rules", []), r["dataset_id"], r["source_stem"], "session")].append(
                        (datetime.strptime(m.group(1), rule["time_format"]), r["file_id"]))
        for ses, lst in stamps.items():
            lst.sort()
            start = prev = None
            for t, f in lst:
                if prev is None or (t - prev).total_seconds() > rule["max_gap_seconds"]:
                    start = t
                burst_of[f] = f"{rule['dataset']}/burst-{start:%Y%m%d-%H%M%S}"
                prev = t
    held = {v["session"]: v["split"] for v in holdout.values()}

    # ---- grouping
    photo = pc.UnionFind()
    split_uf = pc.UnionFind()
    info = {}
    for r in inv:
        f, ds, stem = r["file_id"], r["dataset_id"], r["source_stem"]
        a = pc.asset_id(r)
        fam = pc.match_rule(g["family_rules"], ds, stem, "family")
        seq = pc.match_rule(g.get("sequence_rules", []), ds, stem, "sequence")
        ses = pc.match_rule(g.get("session_rules", []), ds, stem, "session")
        info[f] = {"asset": a, "family": fam or "", "sequence": seq or "", "session": ses or "", "burst": burst_of.get(f, "")}
        for uf in (photo, split_uf):
            uf.union(("file", f), ("asset", a))
            if fam:
                uf.union(("file", f), ("family", fam))
        if seq:
            split_uf.union(("file", f), ("sequence", seq))
        if ses:
            if ses in held or not burst_of.get(f):
                split_uf.union(("file", f), ("session", ses))   # held-out session (or no timestamp): whole day
            else:
                split_uf.union(("file", f), ("burst", burst_of[f]))
    exd_of = {}
    for grp in exd["groups"]:
        for m in grp["file_ids"]:
            exd_of[m] = grp["group_id"]
        for m in grp["file_ids"][1:]:
            photo.union(("file", grp["file_ids"][0]), ("file", m))
            split_uf.union(("file", grp["file_ids"][0]), ("file", m))
    nd_links = 0
    nd_of = defaultdict(set)
    for p in pairs:
        if p["category"] in ("EXACT_DUPLICATE", "NEAR_IDENTICAL_CANDIDATE", "TRANSFORMED_COPY_CANDIDATE"):
            nd_links += 1
            for uf in (photo, split_uf):
                uf.union(("asset", p["asset_a"]), ("asset", p["asset_b"]))
            nd_of[p["asset_a"]].add(p["pair_id"])
            nd_of[p["asset_b"]].add(p["pair_id"])
    # aircraft level: only meaningful with more than one known aircraft in a dataset
    aircraft = {ds: (g.get("aircraft", {}).get(ds) or {}).get("aircraft") for ds in cfg["datasets"]}

    def gid(prefix, root_map, key):
        if key not in root_map:
            root_map[key] = f"{prefix}-{len(root_map) + 1:05d}"
        return root_map[key]
    photo_ids, split_ids = {}, {}
    for r in inv:  # ids numbered in file_id order: deterministic
        f = r["file_id"]
        info[f]["photo_group"] = gid("PG", photo_ids, photo.find(("file", f)))
        info[f]["split_group"] = gid("SG", split_ids, split_uf.find(("file", f)))

    # ---- inclusion status
    unl = {}
    for ds in cfg["datasets"]:
        unl.update({x["file_id"]: x for x in review_rows(f"unlabelled_{ds}.csv")})
    screened = cfg.get("review", {}).get("domain_screen_datasets", [])
    dom = [d for ds in screened for d in review_rows(f"domain_screen_{ds}.csv")]
    dom_by_unit = {d["unit"]: d for d in dom}
    pg_flags = defaultdict(set)
    for r in inv:
        f = r["file_id"]
        unit = info[f]["family"] or info[f]["asset"]
        d = dom_by_unit.get(unit) or dom_by_unit.get(info[f]["asset"])
        if d and d["review_status"] == "NON_AIRCRAFT_SUSPECTED":
            pg_flags[info[f]["photo_group"]].add("NON_AIRCRAFT_SUSPECTED")
        elif d and d["review_status"] in ("UNCLEAR", "AIRCRAFT_NOT_SURFACE_CLOSEUP"):
            pg_flags[info[f]["photo_group"]].add(f"DOMAIN_{d['review_status']}")
    conflict_files = {m for grp in exd["groups"] if grp["annotations"] == "CONFLICT" for m in grp["file_ids"]}
    names = {ds: pc.read_names(pc.RAW / ds / "data.yaml") for ds in cfg["datasets"]}
    rows = []
    for r in inv:
        f, ds = r["file_id"], r["dataset_id"]
        i = info[f]
        a = ann[f]
        reasons, flags = [], []
        st = "RETAINED"
        if r["readable"] != "yes":
            st, reasons = "EXCLUDED", ["UNREADABLE_IMAGE"]
        elif a["annotation_status"] in ("INVALID", "BLOCKED"):
            st, reasons = "EXCLUDED", [f"ANNOTATION_{a['annotation_status']}"]
        else:
            if a["annotation_status"] == "UNLABELLED":
                rv = unl.get(f, {}).get("review_status", "PENDING_REVIEW")
                if rv != "CONFIRMED_NEGATIVE":
                    st = "QUARANTINED"
                    reasons.append(f"UNLABELLED:{rv}")
                else:
                    flags.append("CONFIRMED_NEGATIVE")
            if f in conflict_files:
                st = "QUARANTINED"
                reasons.append("EXACT_DUPLICATE_ANNOTATION_CONFLICT")
            if "NON_AIRCRAFT_SUSPECTED" in pg_flags[i["photo_group"]]:
                st = "QUARANTINED"
                reasons.append("NON_AIRCRAFT_SUSPECTED")
            if a["annotation_status"] == "REQUIRES_REVIEW":
                flags.append("ANNOTATION_REQUIRES_REVIEW")
            if a["annotation_status"] == "VALID_WITH_WARNINGS":
                flags.append("ANNOTATION_CORRECTION_PROPOSED")
            flags += sorted(x for x in pg_flags[i["photo_group"]] if x.startswith("DOMAIN_"))
            if ds in screened and i["session"] and not dom_by_unit.get(i["asset"]):
                flags.append("DOMAIN_NOT_SCREENED")
        src = [c for c in a["source_classes"].split("|") if c]
        rows.append({
            "file_id": f, "dataset_id": ds, "rel_path": r["rel_path"], "sha256": r["sha256"], "source_split": r["source_split"],
            "asset_id": i["asset"], "photo_group": i["photo_group"], "split_group": i["split_group"], "capture_family": i["family"],
            "video_sequence": i["sequence"], "session": i["session"], "burst": i["burst"], "aircraft": aircraft.get(ds) or "unknown",
            "exact_duplicate_group": exd_of.get(f, ""), "near_duplicate_links": len(nd_of.get(i["asset"], ())),
            "annotation_status": a["annotation_status"], "n_objects": a["n_objects"], "source_classes": "|".join(src),
            "mapped_classes": "|".join(sorted({applied[(ds, c)] for c in src if (ds, c) in applied})),
            "unresolved_candidate_classes": "|".join(sorted({f"{c}->{candidate[(ds, c)]}" for c in src if (ds, c) in candidate})),
            "inclusion_status": st, "inclusion_reasons": "|".join(reasons), "flags": "|".join(flags),
            "assigned_split": "", "eval_representative": "", "evaluation_scope": "",
        })

    # ---- grouped allocation (per dataset)
    groups = defaultdict(lambda: {"files": [], "photos": set(), "cls_photos": defaultdict(set)})
    for row in rows:
        G = groups[row["split_group"]]
        G["files"].append(row)
        G["dataset"] = row["dataset_id"]
        if row["session"]:
            G.setdefault("sessions", set()).add(row["session"])
        if row["inclusion_status"] == "RETAINED":
            G["photos"].add(row["photo_group"])
            for c in row["source_classes"].split("|"):
                if c:
                    G["cls_photos"][c].add(row["photo_group"])
    assignment = {}
    alloc_report = {}
    for ds in cfg["datasets"]:
        mine = [k for k, v in groups.items() if v["dataset"] == ds]
        total = Counter()
        for k in mine:
            total["_photos"] += len(groups[k]["photos"])
            for c, s in groups[k]["cls_photos"].items():
                total[c] += len(s)
        cur = {s: Counter() for s in ORDERED}
        purity = []

        def allowed(k):
            ss = groups[k].get("sessions", set())
            forced = {held[x] for x in ss if x in held}
            if forced:
                if ss - set(held):
                    purity.append(k)  # a held-out-session group also holds photos of another session
                return sorted(forced)
            if ss and any(v["session"].split("/")[0] == ds for v in holdout.values()):
                return [s for s in ORDERED if s not in set(held.values())]
            return ORDERED
        order = sorted(mine, key=lambda k: (len(allowed(k)) > 1, -len(groups[k]["photos"]), -len(groups[k]["files"]), pc.stable_hash(seed, k)))
        for k in order:
            vec = Counter({"_photos": len(groups[k]["photos"])})
            for c, s in groups[k]["cls_photos"].items():
                vec[c] += len(s)
            dims = [d for d in vec if vec[d] > 0] or ["_photos"]

            def score(s):
                return sum((targets[s] * total[d] - cur[s][d]) / total[d] for d in dims if total[d]) / len(dims)
            best = max(allowed(k), key=lambda s: (score(s), -ORDERED.index(s)))
            assignment[k] = best
            cur[best].update(vec)
        alloc_report[ds] = {"split_groups": len(mine), "retained_photo_groups": total["_photos"],
                            "photo_groups_by_split": {s: cur[s]["_photos"] for s in ORDERED},
                            "photo_fraction_by_split": {s: round(cur[s]["_photos"] / total["_photos"], 4) if total["_photos"] else None for s in ORDERED},
                            "largest_split_groups_photos": sorted((len(groups[k]["photos"]) for k in mine), reverse=True)[:5],
                            "session_holdout": holdout.get(ds), "session_purity_exceptions": purity}
    scope = cfg.get("evaluation_scope", {})
    by_pg = defaultdict(list)
    for row in rows:
        row["assigned_split"] = assignment[row["split_group"]]
        row["evaluation_scope"] = scope.get(row["dataset_id"], "not documented")
        by_pg[row["photo_group"]].append(row)
    # evaluation representatives: one RETAINED file per photo group in valid/test (copies of one photo are not independent)
    for pg, rs in by_pg.items():
        ok = [x for x in rs if x["inclusion_status"] == "RETAINED"]
        if not ok:
            continue
        if ok[0]["assigned_split"] == "train":
            for x in ok:
                x["eval_representative"] = "n/a (train)"
            continue
        rep = sorted(ok, key=lambda x: (x["source_split"] not in ("valid", "test"), x["file_id"]))[0]
        for x in ok:
            x["eval_representative"] = "yes" if x is rep else "no"

    # ---- write outputs
    fields = list(rows[0].keys())
    pdir, sdir, qdir = pc.out_dir("processed", cfg), pc.out_dir("splits", cfg), pc.out_dir("quarantine", cfg)
    pc.write_csv(pdir / "derived_manifest.csv", rows, fields)
    pc.write_csv(qdir / "quarantine.csv", [{"file_id": r["file_id"], "rel_path": r["rel_path"], "inclusion_status": r["inclusion_status"],
                                            "reasons": r["inclusion_reasons"], "assigned_split": r["assigned_split"], "photo_group": r["photo_group"]}
                                           for r in rows if r["inclusion_status"] != "RETAINED"],
                 ["file_id", "rel_path", "inclusion_status", "reasons", "assigned_split", "photo_group"])
    split_fields = ["file_id", "dataset_id", "rel_path", "sha256", "photo_group", "split_group", "source_split", "annotation_status",
                    "source_classes", "mapped_classes", "unresolved_candidate_classes", "flags", "eval_representative", "evaluation_scope"]
    for s in ORDERED:
        pc.write_csv(sdir / f"{s}.csv", [{k: r[k] for k in split_fields} for r in rows if r["assigned_split"] == s and r["inclusion_status"] == "RETAINED"], split_fields)
    grows = []
    for k in sorted(groups, key=lambda k: int(k.split("-")[1])):
        G = groups[k]
        grows.append({"split_group": k, "dataset_id": G["dataset"], "assigned_split": assignment[k], "files": len(G["files"]),
                      "retained_photo_groups": len(G["photos"]), "assets": len({x["asset_id"] for x in G["files"]}),
                      "sequences": "|".join(sorted({x["video_sequence"] for x in G["files"] if x["video_sequence"]})),
                      "sessions": "|".join(sorted({x["session"] for x in G["files"] if x["session"]})),
                      "source_classes_photo_groups": "|".join(f"{c}:{len(v)}" for c, v in sorted(G["cls_photos"].items()))})
    pc.write_csv(sdir / "groups.csv", grows)
    # negative / normal-surface inventory (no verified negatives are invented)
    neg = []
    for r in rows:
        if r["annotation_status"] == "UNLABELLED":
            rv = unl.get(r["file_id"], {})
            neg.append({"file_id": r["file_id"], "category": "VERIFIED_NORMAL" if rv.get("review_status") == "CONFIRMED_NEGATIVE" else
                        "MISSING_OR_INCOMPLETE_ANNOTATION" if rv.get("review_status") == "DEFECT_PRESENT_MISSING_ANNOTATION" else
                        "UNCERTAIN_DEFECT_STATUS", "review_status": rv.get("review_status", "PENDING_REVIEW"), "assigned_split": r["assigned_split"]})
    pc.write_csv(pdir / "negative_inventory.csv", neg, ["file_id", "category", "review_status", "assigned_split"])

    def stats(sel):
        out = {"files": len(sel), "photo_groups": len({r["photo_group"] for r in sel}), "split_groups": len({r["split_group"] for r in sel}),
               "eval_representatives": sum(1 for r in sel if r["eval_representative"] == "yes"),
               "images_containing_source_class": dict(Counter(c for r in sel for c in r["source_classes"].split("|") if c)),
               "photo_groups_containing_source_class": {c: len({r["photo_group"] for r in sel if c in r["source_classes"].split("|")})
                                                        for c in sorted({c for r in sel for c in r["source_classes"].split("|") if c})},
               "photo_groups_containing_mapped_class": {c: len({r["photo_group"] for r in sel if c in r["mapped_classes"].split("|")})
                                                        for c in sorted({c for r in sel for c in r["mapped_classes"].split("|") if c})}}
        return out
    summary = {"run": pc.run_info(cfg), "method": {"unit": sp["unit"], "seed": seed, "targets": targets, "allocation": "per dataset; groups ordered by "
               "(retained photo groups desc, files desc, sha256(seed, group id)); each group goes to the split with the largest mean "
               "normalised deficit over photo groups and the source classes the group contains", "near_duplicate_links_used": nd_links},
               "allocation": alloc_report, "inclusion": dict(Counter(r["inclusion_status"] for r in rows)),
               "inclusion_reasons": dict(Counter(x for r in rows for x in r["inclusion_reasons"].split("|") if x)),
               "flags": dict(Counter(x for r in rows for x in r["flags"].split("|") if x)),
               "splits": {ds: {s: stats([r for r in rows if r["dataset_id"] == ds and r["assigned_split"] == s and r["inclusion_status"] == "RETAINED"])
                               for s in ORDERED} for ds in cfg["datasets"]},
               "combined": {s: stats([r for r in rows if r["assigned_split"] == s and r["inclusion_status"] == "RETAINED"]) for s in ORDERED},
               "evaluation_scope": scope, "provisional": True,
               "provisional_reason": cfg.get("provisional_reason", "")}
    import hashlib
    summary["assignment_sha256"] = hashlib.sha256("\n".join(f"{r['file_id']}\t{r['assigned_split']}\t{r['inclusion_status']}"
                                                            for r in sorted(rows, key=lambda r: r["file_id"])).encode()).hexdigest()
    pc.write_json(sdir / "split_summary.json", summary)
    pc.log("grouped_splits", cfg, inclusion=summary["inclusion"], groups=len(groups),
           photo_fraction={ds: v["photo_fraction_by_split"] for ds, v in alloc_report.items()})
    print(f"inclusion: {summary['inclusion']}; reasons: {summary['inclusion_reasons']}")
    for ds, v in alloc_report.items():
        print(f"{ds}: {v['split_groups']} split groups, {v['retained_photo_groups']} retained photo groups -> {v['photo_groups_by_split']} "
              f"{v['photo_fraction_by_split']} largest groups {v['largest_split_groups_photos']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
