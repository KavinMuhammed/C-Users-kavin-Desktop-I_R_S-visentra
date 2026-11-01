"""Step 3 — near-duplicate CANDIDATES from perceptual hashes (read-only on raw data).

    python scripts/datasets/find_near_duplicates.py [--prep VT-PREP-001]

Compares every image with every other image (all datasets) using the 64-bit pHash and dHash of the image
and of its 8 rotations/flips, computed by inventory_dataset.py. Thresholds come from the preparation config.

Pair categories (candidates for review, never proof):
  EXACT_DUPLICATE            identical SHA-256
  NEAR_IDENTICAL_CANDIDATE   pHash <= T, dHash <= Td and central-half pHash <= Tc without transformation
  TRANSFORMED_COPY_CANDIDATE the same three conditions only after a rotation/flip
  SIMILAR_UNRESOLVED         best pHash <= review distance, but not the above
Pairs inside one source asset (Roboflow copies of one source image) are expected to be related and are only
counted. Candidate pairs between different assets get a relation hint (same capture family / same inferred
video sequence) but categories 3-5 of the policy (video frame, other photo of the same surface, similar but
distinct defect) need a person to look; a reviewed sample is kept in datasets/review/.

Outputs (datasets/interim/<PREP>/): near_duplicate_pairs.csv, near_duplicate_groups.json, near_duplicate_calibration.json
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def main() -> int:
    import numpy as np
    cfg = pc.load_config()
    nd = cfg["near_duplicates"]
    T, TD, TR = nd["near_identical_max_distance"], nd["require_dhash_agreement_max_distance"], nd["review_max_distance"]
    TC = nd["center_max_distance"]
    g = cfg["grouping"]
    inv = sorted((r for r in pc.load_inventory(cfg) if r["kind"] == "image" and r["readable"] == "yes"), key=lambda r: r["file_id"])
    feats = pc.load_features(cfg)
    n = len(inv)
    if n == 0:
        raise pc.PrepError("no readable images in the inventory")
    P = np.array([[int(h, 16) for h in feats[r["file_id"]]["phash_dihedral"]] for r in inv], dtype=np.uint64)
    D = np.array([[int(h, 16) for h in feats[r["file_id"]]["dhash_dihedral"]] for r in inv], dtype=np.uint64)
    C = np.array([[int(h, 16) for h in feats[r["file_id"]]["phash_center_dihedral"]] for r in inv], dtype=np.uint64)
    sha = np.array([r["sha256"] for r in inv])
    asset = np.array([pc.asset_id(r) for r in inv])
    ds = np.array([r["dataset_id"] for r in inv])
    fam = [pc.match_rule(g["family_rules"], r["dataset_id"], r["source_stem"], "family") for r in inv]
    seq = [pc.match_rule(g.get("sequence_rules", []), r["dataset_id"], r["source_stem"], "sequence") for r in inv]

    def popcount(x):
        return np.bitwise_count(x).astype(np.uint8) if hasattr(np, "bitwise_count") else \
            np.unpackbits(x.view(np.uint8).reshape(*x.shape, 8), axis=-1).sum(-1).astype(np.uint8)

    hist = {k: Counter() for k in ("same_asset_identity", "same_asset_best", "cross_dataset_identity", "cross_dataset_best",
                                   "within_dataset_other_asset_identity", "within_dataset_other_asset_best")}
    match_counts = Counter()
    pairs = []
    step = 400
    for i0 in range(0, n, step):
        i1 = min(n, i0 + step)
        ph = np.stack([popcount(P[i0:i1, k, None] ^ P[None, :, 0]) for k in range(8)])  # (8, rows, n)
        dh = np.stack([popcount(D[i0:i1, k, None] ^ D[None, :, 0]) for k in range(8)])
        ch = np.stack([popcount(C[i0:i1, k, None] ^ C[None, :, 0]) for k in range(8)])
        ok = (ph <= T) & (dh <= TD) & (ch <= TC)
        ident_ok = ok[0]
        any_ok = ok.any(0)
        best = ph.min(0)
        bestk = ph.argmin(0)
        upper = np.arange(n)[None, :] > np.arange(i0, i1)[:, None]
        same_asset = asset[i0:i1, None] == asset[None, :]
        cross_ds = ds[i0:i1, None] != ds[None, :]
        for name, mask, arr in (("same_asset_identity", same_asset, ph[0]), ("same_asset_best", same_asset, best),
                                ("cross_dataset_identity", cross_ds, ph[0]), ("cross_dataset_best", cross_ds, best),
                                ("within_dataset_other_asset_identity", ~same_asset & ~cross_ds, ph[0]),
                                ("within_dataset_other_asset_best", ~same_asset & ~cross_ds, best)):
            v, c = np.unique(arr[mask & upper], return_counts=True)
            hist[name].update(dict(zip(v.tolist(), c.tolist())))
        for name, mask in (("same_asset", same_asset), ("cross_dataset", cross_ds), ("within_dataset_other_asset", ~same_asset & ~cross_ds)):
            match_counts[name + "_near_identical"] += int((ident_ok & mask & upper).sum())
            match_counts[name + "_transformed_only"] += int((any_ok & ~ident_ok & mask & upper).sum())
        exact = sha[i0:i1, None] == sha[None, :]
        keep = upper & ~same_asset & ((best <= TR) | exact)
        for a, b in zip(*np.nonzero(keep)):
            ia, ib = int(a + i0), int(b)
            k = int(bestk[a, b])
            if exact[a, b]:
                cat = "EXACT_DUPLICATE"
            elif ident_ok[a, b]:
                cat = "NEAR_IDENTICAL_CANDIDATE"
            elif any_ok[a, b]:
                cat = "TRANSFORMED_COPY_CANDIDATE"
                k = int(np.argmax(ok[:, a, b]))
            else:
                cat = "SIMILAR_UNRESOLVED"
            ra, rb = inv[ia], inv[ib]
            hint = "same capture family" if fam[ia] and fam[ia] == fam[ib] else \
                "same inferred video sequence" if seq[ia] and seq[ia] == seq[ib] else \
                "different datasets" if ra["dataset_id"] != rb["dataset_id"] else "no known relation"
            pairs.append({"file_a": ra["file_id"], "file_b": rb["file_id"], "asset_a": asset[ia], "asset_b": asset[ib],
                          "phash_identity": int(ph[0, a, b]), "dhash_identity": int(dh[0, a, b]),
                          "best_variant": k, "phash_at_variant": int(ph[k, a, b]), "dhash_at_variant": int(dh[k, a, b]),
                          "center_phash_at_variant": int(ch[k, a, b]),
                          "category": cat, "relation_hint": hint})
    order = {"EXACT_DUPLICATE": 0, "NEAR_IDENTICAL_CANDIDATE": 1, "TRANSFORMED_COPY_CANDIDATE": 2, "SIMILAR_UNRESOLVED": 3}
    pairs.sort(key=lambda p: (order[p["category"]], p["phash_at_variant"], p["file_a"], p["file_b"]))
    for i, p in enumerate(pairs, 1):
        p["pair_id"] = f"NDP-{i:06d}"
    # candidate groups: assets linked by exact / near-identical / transformed pairs
    uf = pc.UnionFind()
    for p in pairs:
        if p["category"] != "SIMILAR_UNRESOLVED":
            uf.union(p["asset_a"], p["asset_b"])
    comp = defaultdict(set)
    for p in pairs:
        if p["category"] != "SIMILAR_UNRESOLVED":
            comp[uf.find(p["asset_a"])].update((p["asset_a"], p["asset_b"]))
    groups = []
    for root in sorted(comp, key=lambda r: (-len(comp[r]), r)):
        groups.append({"group_id": f"NDG-{len(groups) + 1:05d}", "assets": sorted(comp[root]), "n_assets": len(comp[root]),
                       "datasets": sorted({a.split("/")[0] for a in comp[root]}),
                       "status": "CANDIDATE — grouped together for splitting; equivalence not confirmed"})

    def cdf(c):
        tot = sum(c.values())
        acc, out = 0, []
        for d in range(0, 17):
            acc += c.get(d, 0)
            out.append({"distance_le": d, "pairs": acc, "fraction": acc / tot if tot else None})
        return {"total_pairs": tot, "cdf": out}
    bg = sum(hist["cross_dataset_best"].values())
    calib = {
        "run": pc.run_info(cfg), "thresholds": {"phash": T, "dhash": TD, "center_phash": TC, "review": TR},
        "method": "Background = pairs from different datasets (different photographers, aircraft and capture method; assumed unrelated). "
                  "Related reference = pairs of Roboflow copies of the same source asset (augmented, so many are visually different).",
        "histograms": {k: cdf(v) for k, v in hist.items()},
        "rule_matches": dict(match_counts),
        "background_false_match_rate_upper_95": {
            "near_identical": (3.0 / bg) if match_counts["cross_dataset_near_identical"] == 0 and bg else match_counts["cross_dataset_near_identical"] / bg if bg else None,
            "transformed_or_identical": (3.0 / bg) if match_counts["cross_dataset_near_identical"] + match_counts["cross_dataset_transformed_only"] == 0 and bg else None,
            "note": "rule of three: zero matches in N background pairs gives a 95% upper bound of 3/N per pair"},
    }
    idir = pc.out_dir("interim", cfg)
    pc.write_csv(idir / "near_duplicate_pairs.csv", pairs,
                 ["pair_id", "file_a", "file_b", "asset_a", "asset_b", "phash_identity", "dhash_identity", "best_variant",
                  "phash_at_variant", "dhash_at_variant", "center_phash_at_variant", "category", "relation_hint"])
    cat = Counter(p["category"] for p in pairs)
    pc.write_json(idir / "near_duplicate_groups.json", {"run": pc.run_info(cfg), "pair_categories": dict(cat),
                                                         "pair_categories_by_hint": {f"{c} | {h}": v for (c, h), v in sorted(Counter((p['category'], p['relation_hint']) for p in pairs).items())},
                                                         "groups": len(groups), "assets_in_groups": sum(x["n_assets"] for x in groups),
                                                         "largest_groups": [x["n_assets"] for x in groups[:10]], "candidate_groups": groups})
    pc.write_json(idir / "near_duplicate_calibration.json", calib)
    pc.log("near_duplicates", cfg, images=n, pairs=len(pairs), categories=dict(cat), groups=len(groups))
    print(f"{n} images compared ({n * (n - 1) // 2} pairs); candidate pairs between different source assets: {dict(cat)}; "
          f"{len(groups)} candidate groups covering {sum(x['n_assets'] for x in groups)} source assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
