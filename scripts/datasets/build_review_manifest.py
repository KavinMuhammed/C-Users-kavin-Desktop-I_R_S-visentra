"""Step 5 — human-review manifests (and contact-sheet tile lists). Read-only on raw data.

    python scripts/datasets/build_review_manifest.py [--prep VT-PREP-001] [--near-dup-sample 36] [--phone-sample 48]

Creates or updates, under datasets/review/ (committed; reviewers edit the review_* columns):
  unlabelled_<VT-DS-id>.csv     every image whose YOLO label file is empty or missing (Phase 3: 56 in VT-DS-001)
  near_duplicate_sample.csv     deterministic stratified sample of candidate pairs for visual confirmation
  domain_screen_<VT-DS-id>.csv  (datasets in review.domain_screen_datasets) one image per source photo/asset (plus a sample of session photos) to
                                screen for non-aircraft or off-topic content
Existing decisions are preserved: rows are matched by review_id and any row whose review_status is not
PENDING_REVIEW keeps its review columns. Tile lists for generate_contact_sheets.py are written to
datasets/interim/<PREP>/review_lists/.

Review states (unlabelled images): PENDING_REVIEW, CONFIRMED_NEGATIVE, DEFECT_PRESENT_MISSING_ANNOTATION,
ANNOTATION_PRESENT_BUT_INVALID, AMBIGUOUS, UNREADABLE, SOURCE_CLARIFICATION_REQUIRED.
CONFIRMED_NEGATIVE may only be set by a qualified reviewer under the standard in docs/UNLABELLED_IMAGE_REVIEW.md.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402

UNLABELLED_STATES = {"PENDING_REVIEW", "CONFIRMED_NEGATIVE", "DEFECT_PRESENT_MISSING_ANNOTATION", "ANNOTATION_PRESENT_BUT_INVALID",
                     "AMBIGUOUS", "UNREADABLE", "SOURCE_CLARIFICATION_REQUIRED"}
PAIR_STATES = {"PENDING_REVIEW", "SAME_IMAGE", "SAME_IMAGE_TRANSFORMED", "NEAR_IDENTICAL_VIDEO_FRAME", "SAME_SURFACE_DIFFERENT_VIEW",
               "DIFFERENT_IMAGES", "UNRESOLVED"}
DOMAIN_STATES = {"PENDING_REVIEW", "AIRCRAFT_SURFACE", "AIRCRAFT_NOT_SURFACE_CLOSEUP", "NON_AIRCRAFT_SUSPECTED", "UNCLEAR"}
REVIEW_COLS = ["review_status", "assigned_label", "reviewer", "reviewed_at", "review_method", "review_notes", "evidence", "uncertainty"]


def merge(path: Path, rows: list[dict], key: str, states: set) -> tuple[list[dict], int]:
    """Keep existing review decisions for rows that are still present; validate states."""
    kept = 0
    if path.exists():
        old = {r[key]: r for r in pc.read_csv(path)}
        for r in rows:
            o = old.get(r[key])
            if o and o.get("review_status", "PENDING_REVIEW") != "PENDING_REVIEW":
                for c in REVIEW_COLS:
                    if c in o:
                        r[c] = o[c]
                kept += 1
    for r in rows:
        if r["review_status"] not in states:
            raise pc.PrepError(f"{path.name}: {r[key]} has unknown review_status {r['review_status']!r}")
        if r["review_status"] == "CONFIRMED_NEGATIVE" and not (r.get("reviewer") and r.get("evidence")):
            raise pc.PrepError(f"{path.name}: {r[key]} is CONFIRMED_NEGATIVE without reviewer and evidence")
    return rows, kept


def blank(**kw) -> dict:
    return {**kw, "review_status": "PENDING_REVIEW", "assigned_label": "", "reviewer": "", "reviewed_at": "", "review_method": "",
            "review_notes": "", "evidence": "", "uncertainty": ""}


def main() -> int:
    cfg = pc.load_config()
    inv = [r for r in pc.load_inventory(cfg) if r["kind"] == "image"]
    rdir = pc.DS / "review"
    ldir = pc.out_dir("interim", cfg) / "review_lists"
    by_asset = defaultdict(list)
    for r in inv:
        by_asset[pc.asset_id(r)].append(r)

    # ---- 1. images with empty (or missing) label files
    rows = []
    for r in sorted(inv, key=lambda r: r["file_id"]):
        if r["label_status"] not in ("EMPTY_LABEL_FILE", "NO_LABEL_FILE"):
            continue
        sib = by_asset[pc.asset_id(r)]
        lab = [s for s in sib if s["label_status"] == "LABELLED"]
        rows.append(blank(review_id=f"UNL-{r['file_id']}", file_id=r["file_id"], rel_path=r["rel_path"], dataset_id=r["dataset_id"],
                          source_split=r["source_split"], source_asset=pc.asset_id(r), width=r["width"], height=r["height"],
                          readable=r["readable"], existing_annotation_status=r["label_status"], label_path=r["label_path"],
                          copies_of_same_source=len(sib), labelled_copies_of_same_source=len(lab),
                          classes_on_labelled_copies="|".join(sorted({c for s in lab for c in s["source_classes"].split("|") if c}))))
        if r["readable"] != "yes":
            rows[-1].update(review_status="UNREADABLE", reviewer="automatic", review_method="Pillow full decode failed",
                            evidence=r["warnings"])
    for ds in cfg["datasets"]:
        mine = [x for x in rows if x["dataset_id"] == ds]
        if not mine:
            continue
        p = rdir / f"unlabelled_{ds}.csv"
        mine, kept = merge(p, mine, "review_id", UNLABELLED_STATES)
        pc.write_csv(p, mine)
        pc.write_csv(ldir / f"unlabelled_{ds}.csv", [{"file_id": x["file_id"], "caption": f"{x['source_split']} copies={x['copies_of_same_source']} lab={x['labelled_copies_of_same_source']}", "row": ""} for x in mine])
        print(f"{p.relative_to(pc.ROOT)}: {len(mine)} images ({kept} existing decisions kept)")

    # ---- 2. near-duplicate sample (stratified, deterministic; reviewed pairs are always kept)
    pairs = pc.read_csv(pc.DS / "interim" / cfg["prep_id"] / "near_duplicate_pairs.csv")
    n = int(pc.arg("--near-dup-sample", 36))
    by_key = {(p["file_a"], p["file_b"]): p for p in pairs}
    path = rdir / "near_duplicate_sample.csv"
    old = [r for r in pc.read_csv(path)] if path.exists() else []
    reviewed = [r for r in old if r["review_status"] != "PENDING_REVIEW"]
    strata = defaultdict(list)
    for p in pairs:
        if p["relation_hint"] == "same capture family":
            continue  # family links are already grouped by rule; sample the links that change grouping
        strata[(p["category"], p["relation_hint"])].append(p)
    keys = sorted(strata)
    per = max(1, n // max(1, len(keys)))

    def row_for(p, base=None):
        r = base or blank(review_id=f"NDR-{p['file_a']}|{p['file_b']}")
        r.update(file_a=p["file_a"], file_b=p["file_b"], pair_id=p["pair_id"], category=p["category"], relation_hint=p["relation_hint"],
                 phash_identity=p["phash_identity"], dhash_identity=p["dhash_identity"], best_variant=p["best_variant"],
                 phash_at_variant=p["phash_at_variant"], dhash_at_variant=p["dhash_at_variant"],
                 center_phash_at_variant=p["center_phash_at_variant"])
        return r
    prow, taken = [], set()
    for r in reviewed:  # keep every past decision; refresh its current category from this run
        p = by_key.get((r["file_a"], r["file_b"]))
        if p is None:  # pair no longer within the review distance: keep the decision with category NOT_A_CANDIDATE
            r.update(category="NOT_A_CANDIDATE_IN_THIS_RUN", pair_id="")
            prow.append(r)
        else:
            prow.append(row_for(p, dict(r)))
        taken.add((r["file_a"], r["file_b"]))
    for k in keys:
        have = sum(1 for r in prow if (r["category"], r["relation_hint"]) == k)
        for p in sorted(strata[k], key=lambda p: pc.stable_hash(cfg["split"]["seed"], p["file_a"], p["file_b"])):
            if have >= per:
                break
            if (p["file_a"], p["file_b"]) not in taken:
                prow.append(row_for(p))
                taken.add((p["file_a"], p["file_b"]))
                have += 1
    cols = ["review_id", "pair_id", "file_a", "file_b", "category", "relation_hint", "phash_identity", "dhash_identity", "best_variant",
            "phash_at_variant", "dhash_at_variant", "center_phash_at_variant"] + REVIEW_COLS
    prow = [{c: r.get(c, "") for c in cols} for r in prow]
    for r in prow:
        if r["review_status"] not in PAIR_STATES:
            raise pc.PrepError(f"{path.name}: {r['review_id']} has unknown review_status {r['review_status']!r}")
    kept = len(reviewed)
    pc.write_csv(path, prow, cols)
    tiles = []
    for i, p in enumerate(prow, 1):
        tiles.append({"file_id": p["file_a"], "caption": f"S{i} {p['category'][:14]} d={p['phash_at_variant']}/{p['dhash_at_variant']}", "row": f"S{i}"})
        tiles.append({"file_id": p["file_b"], "caption": f"S{i} {p['relation_hint'][:20]}", "row": f"S{i}"})
    pc.write_csv(ldir / "near_duplicate_sample.csv", tiles)
    print(f"{path.relative_to(pc.ROOT)}: {len(prow)} pairs from {len(keys)} strata ({kept} existing decisions kept)")

    # ---- 3. domain screen: one image per source photo / asset + a sample of session photos (configured datasets)
    g = cfg["grouping"]
    k = int(pc.arg("--phone-sample", 48))
    drows_all = 0
    for dsd in cfg.get("review", {}).get("domain_screen_datasets", []):
        units = defaultdict(list)
        phone = []
        for a, rs in by_asset.items():
            ds, stem = a.split("/", 1)
            if ds != dsd:
                continue
            sess = pc.match_rule(g.get("session_rules", []), ds, stem, "session")
            if sess:
                phone.append(a)
                continue
            fam = pc.match_rule(g["family_rules"], ds, stem, "family") or a
            units[fam] += rs
        drows = []
        for u in sorted(units):
            rep = sorted(units[u], key=lambda r: r["file_id"])[0]
            drows.append(blank(review_id=f"DOM-{u}", unit=u, kind="source photo/asset", file_id=rep["file_id"], files_in_unit=len(units[u]),
                               source_classes="|".join(sorted({c for r in units[u] for c in r["source_classes"].split("|") if c}))))
        for a in sorted(phone, key=lambda a: pc.stable_hash(cfg["split"]["seed"], a))[:k]:
            rep = sorted(by_asset[a], key=lambda r: r["file_id"])[0]
            drows.append(blank(review_id=f"DOM-{a}", unit=a, kind="phone-session sample", file_id=rep["file_id"], files_in_unit=len(by_asset[a]),
                               source_classes="|".join(sorted({c for r in by_asset[a] for c in r["source_classes"].split("|") if c}))))
        path = rdir / f"domain_screen_{dsd}.csv"
        drows, kept = merge(path, drows, "review_id", DOMAIN_STATES)
        pc.write_csv(path, drows)
        pc.write_csv(ldir / f"domain_screen_{dsd}.csv", [{"file_id": d["file_id"], "caption": d["unit"].split("/", 1)[1][:30], "row": ""} for d in drows])
        drows_all += len(drows)
        print(f"{path.relative_to(pc.ROOT)}: {len(drows)} units ({len(drows) - min(k, len(phone))} photo/asset units, "
              f"{min(k, len(phone))} of {len(phone)} session photos sampled; {kept} existing decisions kept)")
    pc.log("review_manifests", cfg, unlabelled=len(rows), near_dup_sample=len(prow), domain_units=drows_all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
