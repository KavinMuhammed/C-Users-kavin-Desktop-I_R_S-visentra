"""Import a manually downloaded YOLO dataset export into VisionTra (Phase 13).

    python scripts/datasets/import_dataset.py VT-DS-003 ~/Downloads/aircraft-surface-damage.v3i.yolov8.zip [--dry-run]

Use this for the registered datasets whose hosts this server cannot reach (Roboflow Universe, Kaggle, Zenodo, Mendeley:
status DOWNLOAD_PENDING in datasets/manifests/datasets.json). Download the export yourself (Roboflow: "YOLOv8" or
"YOLOv5 PyTorch" format), then run this script. It

  1. checks that the dataset is registered, not yet acquired, and that its licence permits download;
  2. extracts the archive into a staging directory with safe-extraction rules (no absolute paths, no "..", no links,
     only image / label / metadata file types, size and count limits);
  3. checks the Roboflow YOLO layout (data.yaml with class names; <split>/images and <split>/labels);
  4. writes the per-file manifest datasets/manifests/files/<id>.csv (sha256, size, dimensions, label counts);
  5. moves the staging directory to datasets/raw/<id>/ (never overwrites: raw data is immutable);
  6. records the acquisition in datasets/manifests/datasets.json (status INTEGRITY_VERIFIED);
  7. adds class-mapping entries: an exact synonym of crack, dent or corrosion is mapped PROVISIONAL_ACCEPTED, every
     other source class is NOT_MAPPED (it is ignored by training) — review them in the app (Datasets);
  8. writes a new preparation config datasets/config/prep/VT-PREP-NNN.json that adds the dataset to the latest one.

Then run the preparation pipeline and train the next model version, e.g.

    python scripts/datasets/run_prep.py --prep VT-PREP-002
    python scripts/ml/train_detector.py ml/configs/detection/VT-EXP-005.json   (copy VT-EXP-004 and set the prep id)

Exit codes: 0 imported · 1 refused (nothing changed) · 2 usage error.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DS = ROOT / "datasets"
REGISTRY = DS / "manifests" / "datasets.json"
MAPPING = DS / "config" / "class_mapping.json"
PREP_DIR = DS / "config" / "prep"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
META_EXT = {".txt", ".yaml", ".yml", ".md", ".json"}
MAX_FILES = 200_000
MAX_BYTES = 30 * 1024 ** 3
MAX_MEMBER = 200 * 1024 ** 2
SYNONYMS = {
    "crack": "crack", "cracks": "crack", "cracking": "crack", "crack-damage": "crack",
    "dent": "dent", "dents": "dent", "denting": "dent",
    "corrosion": "corrosion", "rust": "corrosion", "corroded": "corrosion", "corrosion-damage": "corrosion",
}
FIELDS = ["file_id", "dataset_id", "rel_path", "split", "kind", "original_filename", "format", "bytes", "width", "height", "sha256",
          "label_path", "n_objects", "classes", "source_stem", "status"]


class Refused(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def safe_members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    """Validate every archive member; return (info, relative path) for the files to extract."""
    out, total = [], 0
    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        raise Refused(f"the archive has {len(infos)} entries (limit {MAX_FILES})")
    for i in infos:
        name = i.filename.replace("\\", "/")
        if i.is_dir():
            continue
        mode = (i.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise Refused(f"the archive contains a symbolic link: {name}")
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
            raise Refused(f"unsafe path in the archive: {name}")
        if any(p.startswith("__MACOSX") or p == ".DS_Store" for p in parts):
            continue
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXT | META_EXT:
            raise Refused(f"unexpected file type in the archive: {name}")
        if i.file_size > MAX_MEMBER:
            raise Refused(f"{name} is larger than {MAX_MEMBER // 1024 ** 2} MB")
        total += i.file_size
        if total > MAX_BYTES:
            raise Refused("the archive expands to more than 30 GB")
        out.append((i, "/".join(parts)))
    # Roboflow sometimes wraps everything in one top-level folder: strip it so data.yaml is at the root
    tops = {rel.split("/", 1)[0] for _, rel in out}
    if len(tops) == 1 and not any(rel == "data.yaml" for _, rel in out):
        top = tops.pop()
        out = [(i, rel.split("/", 1)[1]) for i, rel in out if "/" in rel]
        if not any(rel == "data.yaml" for _, rel in out):
            raise Refused(f"no data.yaml at the root of the export (looked in {top}/ too)")
    return out


def read_names(yaml_path: Path) -> list[str]:
    text = yaml_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^names:\s*\[(.*?)\]", text, re.M | re.S)
    if m:
        return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"^names:\s*\n((?:\s+.*\n?)+)", text, re.M)
    if m:
        names = []
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("- "):
                names.append(line[2:].strip().strip("'\""))
            elif ":" in line:
                names.append(line.split(":", 1)[1].strip().strip("'\""))
            else:
                break
        if names:
            return names
    raise Refused("data.yaml has no readable 'names' list")


def build_manifest(ds_id: str, stage: Path, names: list[str]) -> tuple[list[dict], dict]:
    from PIL import Image
    rows, counts = [], {"images": 0, "labels": 0, "by_split": {}, "instances": {n: 0 for n in names}, "bad_images": 0}
    for p in sorted(stage.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(stage).as_posix()
        parts = rel.split("/")
        split = parts[0] if len(parts) >= 3 and parts[1] in ("images", "labels") else ""
        ext = p.suffix.lower()
        row = {"file_id": f"{ds_id}:{rel}", "dataset_id": ds_id, "rel_path": f"datasets/raw/{ds_id}/{rel}", "split": split, "original_filename": p.name,
               "format": ext.lstrip("."), "bytes": p.stat().st_size, "width": "", "height": "", "sha256": sha256(p), "label_path": "", "n_objects": "",
               "classes": "", "source_stem": "", "status": "INTEGRITY_VERIFIED"}
        if ext in IMAGE_EXT and split:
            row["kind"] = "image"
            row["source_stem"] = re.sub(r"_(jpg|jpeg|png|webp)\.rf\.[0-9a-f]+$", "", p.stem, flags=re.I)
            try:
                with Image.open(p) as im:
                    im.verify()
                with Image.open(p) as im:
                    row["width"], row["height"] = im.size
            except Exception:                       # noqa: BLE001 — reported, not fatal: the prep pipeline quarantines it
                row["status"] = "DECODE_FAILED"
                counts["bad_images"] += 1
            lab = stage / split / "labels" / (p.stem + ".txt")
            if lab.is_file():
                cls = []
                for line in lab.read_text(encoding="utf-8", errors="replace").splitlines():
                    tok = line.split()
                    if tok and tok[0].isdigit() and int(tok[0]) < len(names):
                        cls.append(names[int(tok[0])])
                row["label_path"] = f"datasets/raw/{ds_id}/{split}/labels/{lab.name}"
                row["n_objects"] = len(cls)
                row["classes"] = "|".join(sorted(set(cls)))
                for c in cls:
                    counts["instances"][c] += 1
            counts["images"] += 1
            counts["by_split"][split] = counts["by_split"].get(split, 0) + 1
        elif ext == ".txt" and split and parts[1] == "labels":
            row["kind"] = "label"
            counts["labels"] += 1
        else:
            row["kind"] = "metadata"
        rows.append(row)
    if not counts["images"]:
        raise Refused("no images were found under <split>/images/ (expected a Roboflow YOLO export)")
    return rows, counts


def mapping_entries(ds_id: str, names: list[str]) -> list[dict]:
    out = []
    for i, n in enumerate(names):
        key = re.sub(r"[\s_]+", "-", n.strip().lower())
        tgt = SYNONYMS.get(key)
        out.append({"dataset": ds_id, "source_class_id": i, "source_name": n, "target": tgt,
                    "status": "PROVISIONAL_ACCEPTED" if tgt else "NOT_MAPPED",
                    "rationale": (f"Import-time exact-name match ('{n}' → {tgt}). Spot-check the boxes before relying on it." if tgt
                                  else f"'{n}' is not one of VisionTra's detection classes (crack, dent, corrosion); not used for training."),
                    "evidence": [f"datasets/raw/{ds_id}/data.yaml names[{i}] = '{n}'"],
                    "open_question": "No source definition reviewed by a qualified inspector." if tgt else None})
    return out


def next_prep(datasets: list[str]) -> tuple[str, dict]:
    existing = sorted(PREP_DIR.glob("VT-PREP-*.json"))
    latest = json.loads(existing[-1].read_text(encoding="utf-8"))
    n = int(existing[-1].stem.split("-")[-1]) + 1
    pid = f"VT-PREP-{n:03d}"
    cfg = {**latest, "prep_id": pid, "datasets": datasets,
           "description": f"Adds {', '.join(d for d in datasets if d not in latest['datasets'])} to {latest['prep_id']} (import_dataset.py, {now()[:10]})."}
    return pid, cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset_id")
    ap.add_argument("archive", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="validate and report; change nothing")
    a = ap.parse_args(argv)
    try:
        if not re.fullmatch(r"VT-DS-\d{3}", a.dataset_id):
            raise Refused("dataset id must look like VT-DS-003")
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        entry = next((d for d in reg["datasets"] if d["id"] == a.dataset_id), None)
        if entry is None:
            raise Refused(f"{a.dataset_id} is not registered in {REGISTRY.relative_to(ROOT)}")
        if entry["status"] in ("INTEGRITY_VERIFIED", "DOWNLOADED", "PARTIAL"):
            raise Refused(f"{a.dataset_id} is already acquired (status {entry['status']})")
        if entry["status"] in ("EXCLUDED", "RESTRICTED"):
            raise Refused(f"{a.dataset_id} is {entry['status']}: {entry.get('decision')}")
        lic = entry.get("licence") or {}
        if lic.get("download_permitted") is not True:
            raise Refused(f"the licence of {a.dataset_id} is not confirmed to permit download ({lic.get('name') or 'unknown'}); record a licence decision first")
        raw = DS / "raw" / a.dataset_id
        if raw.exists():
            raise Refused(f"{raw.relative_to(ROOT)} already exists; raw data is immutable")
        if not a.archive.is_file():
            raise Refused(f"archive not found: {a.archive}")
        arch_sha = sha256(a.archive)
        stage = DS / "raw" / f".{a.dataset_id}.staging"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            with zipfile.ZipFile(a.archive) as zf:
                members = safe_members(zf)
                for info, rel in members:
                    dst = stage / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
            names = read_names(stage / "data.yaml")
            rows, counts = build_manifest(a.dataset_id, stage, names)
            maps = mapping_entries(a.dataset_id, names)
            print(json.dumps({"dataset": a.dataset_id, "archive_sha256": arch_sha, "classes": names, "counts": counts,
                              "mapping": {m["source_name"]: m["target"] for m in maps}}, indent=1))
            if a.dry_run:
                print("dry run: nothing changed")
                return 0
            fm = DS / "manifests" / "files" / f"{a.dataset_id}.csv"
            with open(fm, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            os.replace(stage, raw)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        acq_n = 1 + max([int(d["acquisition"]["acquisition_id"].split("-")[-1]) for d in reg["datasets"] if isinstance(d.get("acquisition"), dict) and d["acquisition"].get("acquisition_id")] or [0])
        entry["status"] = "INTEGRITY_VERIFIED"
        entry["acquisition"] = {"acquisition_id": f"VT-ACQ-{acq_n:03d}", "acquired_at": now(), "archive_sha256": arch_sha, "archive_name": a.archive.name,
                                "method": "manual download by the operator; imported with scripts/datasets/import_dataset.py",
                                "extract_result": {"complete": True, "files": len(rows), "images": counts["images"], "labels": counts["labels"],
                                                   "decode_failed": counts["bad_images"], "at": now()},
                                "location": f"datasets/raw/{a.dataset_id}/", "file_manifest": f"datasets/manifests/files/{a.dataset_id}.csv"}
        entry["measured"] = {**(entry.get("measured") or {}), "images": counts["images"], "images_by_split": counts["by_split"],
                             "class_names": names, "instances_by_class": counts["instances"]}
        reg["generated_at"] = now()
        write_json(REGISTRY, reg)
        m = json.loads(MAPPING.read_text(encoding="utf-8"))
        m["entries"] = [e for e in m["entries"] if e["dataset"] != a.dataset_id] + maps
        m["version"] = int(m.get("version", 1)) + 1
        m["updated"] = now()[:10]
        m.setdefault("decision_log", []).append({"date": now()[:10], "by": "import_dataset.py",
                                                 "decision": f"Added {a.dataset_id} source classes: " + ", ".join(f"{x['source_name']} → {x['target'] or 'not mapped'}" for x in maps),
                                                 "caveats": ["Exact-name matches only; review the boxes before training on them."]})
        write_json(MAPPING, m)
        pid, cfg = next_prep(sorted({*json.loads(sorted(PREP_DIR.glob('VT-PREP-*.json'))[-1].read_text())["datasets"], a.dataset_id}))
        write_json(PREP_DIR / f"{pid}.json", cfg)
        print(f"imported {a.dataset_id}: {counts['images']} images → datasets/raw/{a.dataset_id}/")
        print(f"next: python scripts/datasets/run_prep.py --prep {pid}")
        return 0
    except Refused as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 1
    except zipfile.BadZipFile:
        print("REFUSED: not a valid zip archive", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
