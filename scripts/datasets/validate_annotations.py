"""Step 4 — structural validation of the source annotations (read-only on raw data and labels).

    python scripts/datasets/validate_annotations.py [--prep VT-PREP-001]

Supported format: YOLO text labels (the only format present in the acquired datasets; checked below).
For each image/label pair: label parses, image exists and decodes, class id integer and < nc of the dataset's
data.yaml, 4 coordinates (box) or an even number >= 6 (polygon), finite values, positive size, extent inside
[0, 1] within the configured tolerance, boxes not smaller than min_box_px, duplicate objects.
Nothing is clipped or fixed. Deterministic corrections (e.g. dropping an exact duplicate line) are written as
PROPOSED, never applied, to datasets/annotations/<PREP>/proposed_corrections.csv.

Image annotation status:
  VALID                  all objects pass
  VALID_WITH_WARNINGS    only RECOVERABLE issues (a deterministic correction is proposed)
  REQUIRES_REVIEW        REVIEW issues (outside image, tiny box) — kept, flagged for a person
  INVALID                any INVALID issue — excluded from derived training data
  UNLABELLED             empty or missing label file — see the unlabelled-image review
  BLOCKED                label file present but image unreadable
Outputs: datasets/interim/<PREP>/annotation_status.csv, annotation_issues.csv; datasets/reports/<PREP>/annotation_validation.json
Exit codes: 0 ok · 1 INVALID annotations found (outputs written) · 2 input error
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_common as pc  # noqa: E402


def main() -> int:
    cfg = pc.load_config()
    a = cfg["annotation"]
    if a.get("format") != "yolo":
        raise pc.PrepError(f"annotation format {a.get('format')!r} is not supported by this script")
    inv = pc.load_inventory(cfg)
    feats = pc.load_features(cfg)
    names = {ds: pc.read_names(pc.RAW / ds / "data.yaml") for ds in cfg["datasets"]}
    for ds, n in names.items():
        if not n:
            raise pc.PrepError(f"{ds}: no class names in data.yaml — cannot validate class ids")
    # what formats are actually present (so unsupported formats are never silently skipped)
    found = Counter()
    for r in inv:
        ext = r["extension"].lower()
        if r["kind"] == "label":
            found["yolo_txt"] += 1
        elif ext in ("xml",):
            found["pascal_voc_xml?"] += 1
        elif ext == "json":
            found["coco_json?"] += 1
    unsupported = {k: v for k, v in found.items() if k != "yolo_txt"}
    status_rows, issue_rows, proposals = [], [], []
    summary = {}
    for ds in cfg["datasets"]:
        nc = len(names[ds])
        st = Counter()
        inst, imgs_with, kinds = Counter(), Counter(), Counter()
        sev = Counter()
        codes = Counter()
        box_px = []
        for r in sorted((x for x in inv if x["dataset_id"] == ds and x["kind"] == "image"), key=lambda x: x["file_id"]):
            lab_rel = r["label_path"].split("/", 3)[-1] if r["label_path"] else ""
            lab_id = f"{ds}:{lab_rel}" if lab_rel else ""
            w = int(r["width"]) if r["width"] else None
            h = int(r["height"]) if r["height"] else None
            if not lab_id:
                status, res = "UNLABELLED", {"objects": [], "issues": []}
            else:
                text = feats.get(lab_id, {}).get("text")
                if text is None:
                    raise pc.PrepError(f"{lab_id}: label text missing from the feature cache — re-run inventory_dataset.py")
                res = pc.parse_yolo(text, nc, w, h, a["box_tolerance"], a["min_box_px"])
                s = {i["severity"] for i in res["issues"]}
                if r["readable"] != "yes":
                    status = "BLOCKED"
                elif "INVALID" in s:
                    status = "INVALID"
                elif "REVIEW" in s:
                    status = "REQUIRES_REVIEW"
                elif not res["objects"] and not res["issues"]:
                    status = "UNLABELLED"
                elif "RECOVERABLE" in s:
                    status = "VALID_WITH_WARNINGS"
                else:
                    status = "VALID"
            st[status] += 1
            present = set()
            for o in res["objects"]:
                cname = names[ds][o["class_id"]]
                inst[cname] += 1
                kinds[o["kind"]] += 1
                present.add(cname)
                if w and h:
                    box_px.append(min(o["w"] * w, o["h"] * h))
            for c in sorted(present):  # sorted: set order depends on PYTHONHASHSEED
                imgs_with[c] += 1
            for i in res["issues"]:
                sev[i["severity"]] += 1
                codes[i["code"]] += 1
                issue_rows.append({"file_id": r["file_id"], "label_path": r["label_path"], "line": i.get("line", ""), "code": i["code"],
                                   "severity": i["severity"], "detail": {k: v for k, v in i.items() if k not in ("line", "code", "severity")} or ""})
                if i["code"] == "DUPLICATE_OBJECT":
                    proposals.append({"file_id": r["file_id"], "label_path": r["label_path"], "line": i["line"],
                                      "correction": f"drop line {i['line']} (identical to line {i['same_as_line']})",
                                      "status": "PROPOSED_NOT_APPLIED", "approved_by": "", "approved_at": ""})
            status_rows.append({"file_id": r["file_id"], "dataset_id": ds, "annotation_status": status, "n_objects": len(res["objects"]),
                                "source_classes": "|".join(sorted(present)), "issue_codes": "|".join(sorted({i["code"] for i in res["issues"]}))})
        box_px.sort()
        q = (lambda p: round(box_px[min(len(box_px) - 1, int(p * len(box_px)))], 1)) if box_px else (lambda p: None)
        summary[ds] = {"class_names": names[ds], "nc": nc, "images": sum(st.values()), "image_annotation_status": dict(st),
                       "objects_by_kind": dict(kinds), "instances_by_source_class": dict(inst),
                       "images_containing_source_class": dict(imgs_with), "issues_by_severity": dict(sev), "issues_by_code": dict(codes),
                       "box_min_side_px_quantiles": {"p01": q(0.01), "p50": q(0.5), "p99": q(0.99)},
                       "objects_min_side_below_8px": sum(1 for v in box_px if v < 8)}
    idir = pc.out_dir("interim", cfg)
    pc.write_csv(idir / "annotation_status.csv", status_rows, ["file_id", "dataset_id", "annotation_status", "n_objects", "source_classes", "issue_codes"])
    pc.write_csv(idir / "annotation_issues.csv", issue_rows, ["file_id", "label_path", "line", "code", "severity", "detail"])
    pc.write_csv(pc.DS / "annotations" / cfg["prep_id"] / "proposed_corrections.csv", proposals,
                 ["file_id", "label_path", "line", "correction", "status", "approved_by", "approved_at"])
    report = {"run": pc.run_info(cfg), "settings": a, "formats_found": dict(found), "unsupported_formats_found": unsupported,
              "datasets": summary, "proposed_corrections": len(proposals), "corrections_applied": 0}
    pc.write_json(pc.out_dir("reports", cfg) / "annotation_validation.json", report)
    invalid = sum(s["image_annotation_status"].get("INVALID", 0) for s in summary.values())
    pc.log("annotation_validation", cfg, invalid_images=invalid, issues=len(issue_rows), unsupported=unsupported)
    for ds, s in summary.items():
        print(f"{ds}: {s['image_annotation_status']} issues={s['issues_by_code']}")
    if unsupported:
        print(f"WARNING: annotation files in unsupported formats were found and NOT validated: {unsupported}")
        return 1
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
