"""Read-only detection-dataset registry, image browser and readiness (Phase 12; Phase 5 EP-20/21/23/24/25 + EP-39/40/41).

Source of truth: the repository's dataset files, produced and validated in Phases 3–4 and never written here:

| What | File |
|---|---|
| Registry, provenance, licences, measured counts | `datasets/manifests/datasets.json` |
| Class mapping CMAP-001 | `datasets/config/class_mapping.json` |
| Prepared version VT-PREP-001: one row per file, with sha256, groups, annotation status, inclusion and split | `datasets/processed/VT-PREP-001/derived_manifest.csv` |
| Quality, annotation and split reports | `datasets/reports/VT-PREP-001/*.json`, `datasets/splits/VT-PREP-001/split_summary.json` |
| Original images and YOLO labels | `datasets/raw/<dataset>/<split>/{images,labels}` |

**Rules:**
- Nothing is copied into the frontend or served from a static URL. Images are read only through paths listed in the manifest, resolved under `datasets/raw` (no user-supplied path is ever used).
- Thumbnails are JPEG previews made on demand. They are cached under the data directory and keyed by the file's SHA-256, the size and the renderer version. The original bytes are never modified.
- Held-out **test** images are listed and counted but **not displayed**, so that nobody tunes anything on the evaluation set.
- Images of a dataset whose licence is not verified are not displayed either.
- Labels are shown with their **source** class names. A target class (crack, dent, corrosion) is shown only where the class mapping applies it. UNRESOLVED and NOT_MAPPED classes stay unresolved.
- Readiness reuses the Phase 7 training gate (`visentra_ml.detection.data.readiness`). A request for classes without an approved mapping is refused with the mapping's own reason.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
from collections import Counter
from pathlib import Path

from . import errors as E
from .config import settings
from .errors import api_error

THUMB_VERSION = "t1"
SIZES = {"thumb": 320, "preview": 1280}
PREP_ID = "VT-PREP-001"
VERIFIED_LICENCE = {"VERIFIED", "VERIFIED_WITH_NOTE"}
_lock = threading.Lock()
_cache: dict = {}


def root() -> Path:
    env = os.environ.get("VISIONTRA_DATASETS_ROOT")
    if env:
        return Path(env).resolve()
    from visentra_ml.detection.config import ROOT
    return (ROOT / "datasets").resolve()


def _cached(name: str, path: Path, load):
    """Load a file once per modification time (manifests are regenerated only by the offline Phase 3–4 scripts)."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    key = (name, st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
    val = load(path)
    with _lock:
        _cache[name] = (key, val)
    return val


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def registry() -> dict:
    d = _cached("registry", root() / "manifests" / "datasets.json", _json)
    if d is None:
        raise api_error(503, E.SERVICE_UNAVAILABLE, "The dataset registry is not present on this server (datasets/manifests/datasets.json).")
    return d


def mapping() -> dict:
    m = _cached("mapping", root() / "config" / "class_mapping.json", _json)
    if m is None:
        raise api_error(503, E.SERVICE_UNAVAILABLE, "The class mapping is not present on this server.")
    return m


def _load_manifest(p: Path) -> dict:
    rows, by_id = [], {}
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["image_id"] = hashlib.sha256(r["file_id"].encode()).hexdigest()[:24]
            r["n_objects"] = int(r["n_objects"] or 0)
            rows.append(r)
            by_id[r["image_id"]] = r
    return {"rows": rows, "by_id": by_id}


def manifest() -> dict:
    m = _cached("manifest", root() / "processed" / PREP_ID / "derived_manifest.csv", _load_manifest)
    if m is None:
        raise api_error(503, E.SERVICE_UNAVAILABLE, f"The prepared dataset {PREP_ID} is not present on this server.")
    return m


def _report(name: str):
    return _cached(f"report:{name}", root() / "reports" / PREP_ID / name, _json)


def _entry(ds_id: str) -> dict:
    for d in registry()["datasets"]:
        if d["id"] == ds_id:
            return d
    raise api_error(404, E.NOT_FOUND, "Dataset not found.")


def _available(ds_id: str) -> bool:
    return (root() / "raw" / ds_id).is_dir()


def licence_ok(d: dict) -> bool:
    lic = d.get("licence") or {}
    return lic.get("status") in VERIFIED_LICENCE and bool(lic.get("download_permitted"))


def dataset_out(d: dict, full: bool = False) -> dict:
    lic = d.get("licence") or {}
    out = {"id": d["id"], "name": d["name"], "publisher": d.get("publisher"), "landing_url": d.get("landing_url"), "status": d.get("status"),
           "decision": d.get("decision"), "domain": d.get("domain"), "task": d.get("task"), "version": d.get("version"),
           "licence": {"name": lic.get("name"), "status": lic.get("status") or "UNKNOWN", "download_permitted": lic.get("download_permitted"),
                       "redistribution": lic.get("redistribution"), "attribution": lic.get("attribution"), "commercial_use": lic.get("commercial_use")},
           "intended_use_compatible": d.get("intended_use_compatible"), "locally_available": _available(d["id"]),
           "images_viewable": _available(d["id"]) and licence_ok(d), "measured": d.get("measured"), "source_classes": d.get("source_classes")}
    if full:
        out.update({k: d.get(k) for k in ("download_url", "download_route", "annotation_format", "media", "relevance", "stated_counts", "known_limitations", "acquisition")})
        out["licence"] = lic
    return out


# ---------------------------------------------------------------------------------------------- EP-20 / 24 / 25
def list_datasets() -> dict:
    reg = registry()
    items = [dataset_out(d) for d in reg["datasets"]]
    st = Counter(x["status"] for x in items)
    return {"items": items, "total": len(items), "by_status": dict(st), "generated_at": reg.get("generated_at"),
            "versions": [version_summary()], "source": "datasets/manifests/datasets.json (read-only)"}


def get_dataset(ds_id: str) -> dict:
    return dataset_out(_entry(ds_id), full=True)


def sources() -> dict:
    return {"items": [{"dataset_id": d["id"], "name": d["name"], "publisher": d.get("publisher"), "landing_url": d.get("landing_url"),
                       "licence": d.get("licence"), "status": d.get("status"), "decision": d.get("decision")} for d in registry()["datasets"]]}


def class_mappings(dataset: str | None = None) -> dict:
    m = mapping()
    ents = [e for e in m["entries"] if not dataset or e["dataset"] == dataset]
    if dataset and not ents:
        raise api_error(422, E.VALIDATION, "No mapping entries for this dataset.", field="dataset")
    return {"mapping_id": m.get("mapping_id"), "mapping_version": m.get("version"), "taxonomy": m.get("taxonomy"), "updated": m.get("updated"),
            "target_classes": m.get("target_classes"), "status_definitions": m.get("status_definitions"), "items": ents,
            "summary": dict(Counter(e["status"] for e in ents))}


# ---------------------------------------------------------------------------------------------- EP-21 version
def version_summary() -> dict:
    q = _report("data_quality_report.json") or {}
    return {"id": PREP_ID, "datasets": sorted(q.get("datasets", {}).keys()), "generated_by": (q.get("run") or {}).get("generated_by"),
            "config_sha256": (q.get("run") or {}).get("config_sha256")}


def get_version(version_id: str) -> dict:
    if version_id != PREP_ID:
        raise api_error(404, E.NOT_FOUND, "Dataset version not found.")
    rows = manifest()["rows"]
    split = Counter(r["assigned_split"] for r in rows)
    incl = Counter(r["inclusion_status"] for r in rows)
    ann = Counter(r["annotation_status"] for r in rows)
    src = Counter()
    mapped = Counter()
    for r in rows:
        for c in filter(None, r["source_classes"].split("|")):
            src[c] += 1
        for c in filter(None, r["mapped_classes"].split("|")):
            mapped[c] += 1
    q = _report("data_quality_report.json") or {}
    splits = _cached("split_summary", root() / "splits" / PREP_ID / "split_summary.json", _json) or {}
    return {"id": PREP_ID, "files": len(rows), "by_split": dict(split), "by_inclusion": dict(incl), "by_annotation_status": dict(ann),
            "files_containing_source_class": dict(src), "files_containing_mapped_class": dict(mapped),
            "evaluation_scope": rows[0]["evaluation_scope"] if rows else None,
            "split_method": splits.get("method"), "split_allocation": splits.get("allocation"),
            "quality": {k: q.get(k) for k in ("datasets", "exact_duplicates", "near_duplicates", "negatives", "class_mapping", "licence_and_provenance_issues")},
            "annotation_validation": _report("annotation_validation.json"), "split_validation": _report("split_validation.json"),
            "class_mapping_report": _report("class_mapping_report.json"),
            "source": f"datasets/processed/{PREP_ID}/derived_manifest.csv and datasets/reports/{PREP_ID} (read-only)"}


# ---------------------------------------------------------------------------------------------- EP-39 image list
FILTERS = ("dataset", "split", "source_class", "mapped_class", "annotation_status", "inclusion_status", "flag")


def _row_out(r: dict) -> dict:
    split = r["assigned_split"]
    ds = _entry(r["dataset_id"])
    viewable = split != "test" and licence_ok(ds) and _available(r["dataset_id"])
    out = {"image_id": r["image_id"], "file_id": r["file_id"], "dataset_id": r["dataset_id"], "file_name": r["rel_path"].rsplit("/", 1)[-1],
           "sha256": r["sha256"], "source_split": r["source_split"], "assigned_split": split, "photo_group": r["photo_group"], "split_group": r["split_group"],
           "annotation_status": r["annotation_status"], "n_objects": r["n_objects"],
           "source_classes": [c for c in r["source_classes"].split("|") if c], "mapped_classes": [c for c in r["mapped_classes"].split("|") if c],
           "unresolved_candidate_classes": [c for c in r["unresolved_candidate_classes"].split("|") if c],
           "inclusion_status": r["inclusion_status"], "inclusion_reasons": [c for c in r["inclusion_reasons"].split("|") if c],
           "flags": [c for c in r["flags"].split("|") if c], "exact_duplicate_group": r["exact_duplicate_group"] or None,
           "viewable": viewable}
    if not viewable:
        out["not_viewable_reason"] = ("held-out test image: not displayed, to keep the evaluation set untouched" if split == "test"
                                      else "dataset licence not verified for display" if not licence_ok(ds) else "image files are not on this server")
    else:
        out["thumb_url"] = f"/api/v1/detection-dataset-versions/{PREP_ID}/images/{r['image_id']}/thumb"
    return out


def list_images(version_id: str, page: int, per_page: int, filters: dict) -> dict:
    if version_id != PREP_ID:
        raise api_error(404, E.NOT_FOUND, "Dataset version not found.")
    if not (1 <= per_page <= 120) or page < 1:
        raise api_error(422, E.VALIDATION, "page ≥ 1 and 1 ≤ per_page ≤ 120.", field="per_page")
    rows = manifest()["rows"]
    f = {k: v for k, v in filters.items() if v}
    def keep(r):
        if f.get("dataset") and r["dataset_id"] != f["dataset"]:
            return False
        if f.get("split") and r["assigned_split"] != f["split"]:
            return False
        if f.get("annotation_status") and r["annotation_status"] != f["annotation_status"]:
            return False
        if f.get("inclusion_status") and r["inclusion_status"] != f["inclusion_status"]:
            return False
        if f.get("source_class") and f["source_class"] not in r["source_classes"].split("|"):
            return False
        if f.get("mapped_class") and f["mapped_class"] not in r["mapped_classes"].split("|"):
            return False
        if f.get("flag") and f["flag"] not in r["flags"].split("|"):
            return False
        return True
    sel = [r for r in rows if keep(r)]
    total = len(sel)
    part = sel[(page - 1) * per_page: page * per_page]
    return {"items": [_row_out(r) for r in part], "total": total, "page": page, "per_page": per_page, "pages": max(1, -(-total // per_page)),
            "filters": f, "facets": {"split": dict(Counter(r["assigned_split"] for r in sel)), "annotation_status": dict(Counter(r["annotation_status"] for r in sel)),
                                     "inclusion_status": dict(Counter(r["inclusion_status"] for r in sel))}}


def _row(version_id: str, image_id: str) -> dict:
    if version_id != PREP_ID:
        raise api_error(404, E.NOT_FOUND, "Dataset version not found.")
    r = manifest()["by_id"].get(image_id) if isinstance(image_id, str) and len(image_id) == 24 else None
    if r is None:
        raise api_error(404, E.NOT_FOUND, "Image not found.")
    return r


def _safe_path(rel: str) -> Path:
    """Resolve a manifest path and refuse anything outside datasets/raw (defence in depth: paths come from the manifest only)."""
    base = (root() / "raw").resolve()
    p = (root().parent / rel).resolve()
    if base not in p.parents:
        raise api_error(404, E.NOT_FOUND, "Image not found.")
    return p


def _require_viewable(r: dict) -> None:
    out = _row_out(r)
    if not out["viewable"]:
        raise api_error(403, E.FORBIDDEN, f"This image is not displayed: {out['not_viewable_reason']}.", reason=out["not_viewable_reason"])


# ---------------------------------------------------------------------------------------------- EP-40 image detail
def image_detail(version_id: str, image_id: str, verify: bool = False) -> dict:
    r = _row(version_id, image_id)
    out = _row_out(r)
    if not out["viewable"]:
        return out
    from PIL import Image
    p = _safe_path(r["rel_path"])
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        out.update(integrity="missing", boxes=[], label_problems=["image file is missing on this server"])
        return out
    out["integrity"] = ("ok" if hashlib.sha256(data).hexdigest() == r["sha256"] else "checksum_mismatch") if verify else "not_checked"
    try:
        with Image.open(io.BytesIO(data)) as im:
            out["width"], out["height"] = im.size
    except Exception:  # noqa: BLE001 - corrupt image is a finding, not a server error
        out["integrity"] = "unreadable"
    from visentra_ml.detection.data import label_path_for
    lp = _safe_path(label_path_for(r["rel_path"]))
    names = _class_names(r["dataset_id"])
    m = mapping()
    applied = {(e["dataset"], int(e["source_class_id"])): e for e in m["entries"]}
    boxes, problems = [], []
    if not lp.exists():
        problems.append("label file missing")
    else:
        for n, line in enumerate(lp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            fs = line.split()
            if not fs:
                continue
            try:
                c, vals = int(fs[0]), [float(v) for v in fs[1:]]
            except ValueError:
                problems.append(f"line {n}: not numeric"); continue
            if len(vals) == 4:
                cx, cy, w, h = vals; x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            elif len(vals) >= 6 and len(vals) % 2 == 0:
                xs, ys = vals[0::2], vals[1::2]; x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            else:
                problems.append(f"line {n}: wrong field count"); continue
            e = applied.get((r["dataset_id"], c))
            target = e["target"] if e and e["status"] in ("ACCEPTED", "PROVISIONAL_ACCEPTED") else None
            boxes.append({"source_class_id": c, "source_class": names[c] if 0 <= c < len(names) else f"class {c}",
                          "mapping_status": e["status"] if e else "NOT_IN_MAPPING", "target_class": target,
                          "box": {"x": round(x1, 6), "y": round(y1, 6), "w": round(x2 - x1, 6), "h": round(y2 - y1, 6)},
                          "polygon": len(vals) > 4, "out_of_bounds": min(x1, y1) < -0.001 or max(x2, y2) > 1.001})
    out.update(boxes=boxes, label_problems=problems, coordinate_frame="fractions of the stored image, EXIF orientation not applied (as in the YOLO export)")
    return out


def _class_names(ds_id: str) -> list[str]:
    def load(p: Path):
        names = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("names:"):
                inner = line.split(":", 1)[1].strip().strip("[]")
                names = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        return names
    return _cached(f"names:{ds_id}", root() / "raw" / ds_id / "data.yaml", load) or []


# ---------------------------------------------------------------------------------------------- EP-41 thumbnail
def thumbnail(version_id: str, image_id: str, size: str) -> tuple[bytes, str]:
    if size not in SIZES:
        raise api_error(422, E.VALIDATION, f"size must be one of {sorted(SIZES)}.", field="size")
    r = _row(version_id, image_id)
    _require_viewable(r)
    px = SIZES[size]
    cache_dir = Path(settings.data_dir) / "cache" / "dataset-previews"
    cp = cache_dir / f"{r['sha256']}-{px}-{THUMB_VERSION}.jpg"
    src = _safe_path(r["rel_path"])
    if not src.exists():                              # checked before the cache: a preview never outlives its original
        raise api_error(404, E.NOT_FOUND, "The image file is missing on this server.", reason="missing")
    if cp.exists():
        return cp.read_bytes(), r["sha256"]
    from PIL import Image
    with Image.open(src) as im:
        im.draft("RGB", (px, px))                     # JPEG: decode at reduced scale (fast, same pixels as a resize)
        im = im.convert("RGB")
        im.thumbnail((px, px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)  # no EXIF, no ICC: metadata-free preview
    data = buf.getvalue()
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, cp)
    return data, r["sha256"]


# ---------------------------------------------------------------------------------------------- EP-23 readiness
_TRAINABLE = ("VT-DS-001", "VT-DS-002")


def readiness(version_id: str, classes: list[str], datasets: list[str]) -> dict:
    """Run the Phase 7 pre-training gate for the requested target classes and datasets (structure and labels; images
    are not opened here, the command-line gate does that). Refuses classes without an approved mapping."""
    if version_id != PREP_ID:
        raise api_error(404, E.NOT_FOUND, "Dataset version not found.")
    names = [t["name"] for t in mapping().get("target_classes", [])]
    bad = [c for c in classes if c not in names]
    if not classes or bad:
        raise api_error(422, E.VALIDATION, f"classes must be one or more of {names}.", field="classes")
    unknown = [d for d in datasets if d not in _TRAINABLE]
    if not datasets or unknown:
        raise api_error(422, E.VALIDATION, f"datasets must be among the prepared datasets {list(_TRAINABLE)}.", field="datasets")
    key = ("readiness", tuple(sorted(classes)), tuple(sorted(datasets)), _mtimes())
    with _lock:
        hit = _cache.get(key)
    if hit:
        return hit
    from visentra_ml.detection import config as C
    from visentra_ml.detection import data as DD
    cfg = json.loads((C.ROOT / "ml" / "configs" / "detection" / "VT-EXP-001.json").read_text(encoding="utf-8"))
    cfg["dataset"]["target_classes"] = list(classes)
    cfg["dataset"]["datasets"] = list(datasets)
    problems = C.validate(cfg, check_paths=True)
    rep = DD.readiness(cfg, check_images=False) if not problems else {"conditions": {}, "ready": False}
    licences = {d: licence_ok(_entry(d)) for d in datasets}
    conds = {k: {"ok": bool(v.get("ok")), "detail": v.get("detail")} for k, v in rep.get("conditions", {}).items()}
    conds["files_readable"] = {"ok": None, "detail": "not run here: images are opened by scripts/ml/validate_training_config.py before any training"}
    conds["files_match_manifest_sha256"] = {"ok": None, "detail": "not run here: use scripts/ml/validate_training_config.py --hash (re-hashes every image)"}
    conds["dataset_licences_verified"] = {"ok": all(licences.values()), "detail": licences}
    conds["verified_negative_images"] = {"ok": False, "detail": "none: empty-label images are UNLABELLED, not verified defect-free (warning, does not block)", "warning": True}
    blocking = {k: c for k, c in conds.items() if c["ok"] is False and not c.get("warning")}
    passed = not problems and not blocking
    out = {"version_id": PREP_ID, "classes": classes, "datasets": datasets, "structural_checks_passed": passed,
           "decision": "PASSED_STRUCTURAL_CHECKS" if passed else "REFUSED",
           "next_step": ("Run scripts/ml/validate_training_config.py --hash on a documented experiment configuration (opens and re-hashes every image) "
                         "before any training." if passed else "Do not train with these classes or datasets until every blocking condition is resolved."),
           "config_problems": problems, "conditions": conds, "blocking": sorted(blocking),
           "splits": {s: {k: v for k, v in d.items() if k in ("images", "photo_groups", "instances", "images_with_target", "unlabelled_images", "label_problem_count")} for s, d in rep.get("splits", {}).items()},
           "note": "Readiness is a data gate, not a model-quality statement. No training is started from the app; VT-DET-0001 is not retrained."}
    with _lock:
        _cache[key] = out
    return out


def _mtimes() -> tuple:
    r = root()
    return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (r / "config" / "class_mapping.json", r / "processed" / PREP_ID / "derived_manifest.csv"))


# ---------------------------------------------------------------------------------------------- Phase 13: what the model learned
def training_examples(per_class: int = 6, seed: int = 13) -> dict:
    """Real labelled training images per target class (train split only, licence-cleared, never test images), with the boxes
    of that class drawn from the source labels through the class mapping. Deterministic for a given seed."""
    import random
    per_class = max(1, min(12, per_class))
    m = mapping()
    targets = [t["name"] for t in m.get("target_classes", []) if t.get("name") != "other"]
    rows = [r for r in manifest()["rows"] if r["assigned_split"] == "train" and r.get("inclusion_status") == "RETAINED"]
    rng = random.Random(seed)
    out, used_groups = {}, set()
    for cls in targets:
        # candidates through the CURRENT mapping (the manifest's mapped_classes column reflects the mapping at preparation time)
        src = {(e["dataset"], e["source_name"]) for e in m["entries"] if e.get("target") == cls and e["status"] in ("ACCEPTED", "PROVISIONAL_ACCEPTED")}
        cands = [r for r in rows if any((r["dataset_id"], n) in src for n in (r["source_classes"] or "").split("|"))]
        rng.shuffle(cands)
        picked = []
        for r in cands:
            if len(picked) >= per_class:
                break
            if r["photo_group"] in used_groups:
                continue
            try:
                d = image_detail(PREP_ID, r["image_id"])
            except Exception:  # noqa: BLE001 — a missing file is skipped, never invented
                continue
            if not d.get("viewable") or d.get("integrity") in ("missing", "unreadable"):
                continue
            boxes = [b["box"] for b in d.get("boxes", []) if b.get("target_class") == cls]
            if not boxes:
                continue
            used_groups.add(r["photo_group"])
            picked.append({"image_id": r["image_id"], "dataset_id": r["dataset_id"], "thumb_url": d["thumb_url"], "boxes": boxes[:12],
                           "n_boxes": len(boxes)})
        out[cls] = {"items": picked, "available_images": len(cands)}
    return {"version": PREP_ID, "split": "train", "classes": out,
            "note": "Real training images and their source labels (mapped to VisionTra classes). Test images are never shown."}
