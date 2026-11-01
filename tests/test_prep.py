"""Tests for the Phase 4 data-preparation scripts (scripts/datasets/*.py) on SYNTHETIC fixtures.

The fixture images are random block textures generated here for testing script behaviour only. They are not
aircraft images, are never written into the repository's datasets/ tree, and live only in pytest's tmp_path.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts" / "datasets"
PREP = "VT-PREP-001"


def texture(seed: int, size: int = 128) -> "np.ndarray":
    rng = np.random.default_rng(seed)
    small = (rng.random((16, 16, 3)) * 255).astype("uint8")
    return np.kron(small, np.ones((size // 16, size // 16, 1), dtype="uint8"))


def jpeg(arr) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, format="JPEG", quality=95)
    return b.getvalue()


def png(arr) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, format="PNG")
    return b.getvalue()


H1, H2 = "0" * 32, "1" * 32


def build_fixture(root: Path) -> dict:
    ds1 = root / "datasets" / "raw" / "VT-DS-901"
    ds2 = root / "datasets" / "raw" / "VT-DS-902"
    files: dict[str, bytes] = {}

    def put(ds, split, name, img: bytes | None, label: str | None):
        if img is not None:
            files[f"{ds}/{split}/images/{name}"] = img
        if label is not None:
            files[f"{ds}/{split}/labels/{Path(name).stem}.txt"] = label.encode()
    box = "0 0.5 0.5 0.2 0.2\n"
    dent = "1 0.4 0.4 0.2 0.2\n"
    D1 = "VT-DS-901"
    put(D1, "train", f"a_jpg.rf.{H1}.jpg", jpeg(texture(1)), box)            # two augmented copies of source 'a'
    put(D1, "train", f"a_jpg.rf.{H2}.jpg", jpeg(texture(2)), box)
    q = texture(3)
    put(D1, "train", "q1.jpg", jpeg(q), box)                                    # near-identical pair, different assets
    put(D1, "valid", "q2.jpg", jpeg(np.clip(q.astype(int) + 12, 0, 255).astype("uint8")), box)
    r = texture(4)
    put(D1, "train", "r1.jpg", jpeg(r), dent)                                   # mirrored copy
    put(D1, "test", "r2.jpg", jpeg(r[:, ::-1].copy()), dent)
    same = jpeg(texture(5))
    put(D1, "train", "dupx.jpg", same, box)                                     # identical bytes, conflicting labels
    put(D1, "test", "dupy.jpg", same, dent)
    put(D1, "train", "badcls.jpg", jpeg(texture(6)), "7 0.5 0.5 0.1 0.1\n")     # invalid class id
    put(D1, "train", "nan.jpg", jpeg(texture(7)), "0 nan 0.5 0.1 0.1\n")        # non-finite coordinate
    put(D1, "train", "fields.jpg", jpeg(texture(8)), "0 0.5 0.5\n")             # wrong field count
    put(D1, "train", "out.jpg", jpeg(texture(9)), "0 0.99 0.5 0.1 0.1\n")       # extends outside the image
    put(D1, "train", "dupline.jpg", jpeg(texture(10)), box + box)               # duplicate object line
    put(D1, "train", "nolabel.jpg", jpeg(texture(11)), None)                    # image without label file
    put(D1, "train", "orphan.jpg", None, box)                                   # label without image
    put(D1, "train", "trunc.jpg", jpeg(texture(12))[:400], box)                 # unreadable (truncated)
    put(D1, "train", "fakeext.jpg", png(texture(13)), box)                      # PNG bytes with .jpg extension
    put(D1, "train", "empty.jpg", jpeg(texture(14)), "")                        # empty label file
    for i in range(20):                                                        # ordinary images for the split
        put(D1, ["train", "valid", "test"][i % 3], f"u{i}.jpg", jpeg(texture(100 + i)), box if i % 2 else dent)
    files[f"{D1}/data.yaml"] = b"nc: 2\nnames: ['crack', 'dent']\n"
    D2 = "VT-DS-902"
    for i in range(10):
        put(D2, ["train", "valid", "test"][i % 3], f"sceneadd{1 + i // 5}{i:05d}.jpg", jpeg(texture(200 + i)), "0 0.5 0.5 0.3 0.3\n")
    files[f"{D2}/data.yaml"] = b"nc: 1\nnames: ['Rust']\n"
    for rel, data in files.items():
        p = root / "datasets" / "raw" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    # Phase 3-style manifests (one duplicated record to be detected)
    man = {"schema": "visiontra.dataset_manifest/1", "datasets": []}
    for ds in (D1, D2):
        rows = []
        for rel, data in sorted(files.items()):
            if rel.startswith(ds + "/"):
                sub = rel.split("/", 1)[1]
                rows.append({"file_id": f"{ds}:{sub}", "rel_path": f"datasets/raw/{rel}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        if ds == D1:
            rows.append(dict(rows[0]))
        fm = root / "datasets" / "manifests" / "files" / f"{ds}.csv"
        fm.parent.mkdir(parents=True, exist_ok=True)
        with open(fm, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        man["datasets"].append({"id": ds, "status": "INTEGRITY_VERIFIED",
                                "acquisition": {"acquisition_id": "VT-ACQ-901", "file_manifest": f"datasets/manifests/files/{ds}.csv"}})
    (root / "datasets" / "manifests" / "datasets.json").write_text(json.dumps(man))
    cfg = {
        "prep_id": PREP, "datasets": [D1, D2], "taxonomy": "TAX-1.0", "class_mapping": "datasets/config/class_mapping.json",
        "annotation": {"format": "yolo", "box_tolerance": 1e-6, "min_box_px": 2.0},
        "near_duplicates": {"near_identical_max_distance": 4, "require_dhash_agreement_max_distance": 10, "center_max_distance": 20, "review_max_distance": 10},
        "grouping": {"family_rules": [], "sequence_rules": [{"dataset": D2, "pattern": "^(scene(?:add)?)(\\d+)\\d{5}$", "sequence": "s-{1}-{2}"}],
                     "session_rules": [], "burst_rules": [], "aircraft": {D2: {"aircraft": "plane-1"}, D1: {"aircraft": None}}},
        "split": {"seed": 7, "targets": {"train": 0.6, "valid": 0.2, "test": 0.2}, "unit": "test"},
        "review": {"domain_screen_datasets": []},
        "evaluation_scope": {D1: "fixture", D2: "fixture single aircraft"},
    }
    (root / "datasets" / "config" / "prep").mkdir(parents=True)
    (root / "datasets" / "config" / "prep" / f"{PREP}.json").write_text(json.dumps(cfg))
    mapping = {"mapping_id": "CMAP-T", "version": 1, "taxonomy": "TAX-1.0",
               "target_classes": [{"id": 0, "name": "crack"}, {"id": 1, "name": "dent"}, {"id": 2, "name": "corrosion"}],
               "entries": [
                   {"dataset": D1, "source_class_id": 0, "source_name": "crack", "target": "crack", "status": "PROVISIONAL_ACCEPTED", "rationale": "t"},
                   {"dataset": D1, "source_class_id": 1, "source_name": "dent", "target": "dent", "status": "PROVISIONAL_ACCEPTED", "rationale": "t"},
                   {"dataset": D2, "source_class_id": 0, "source_name": "Rust", "target": "corrosion", "status": "UNRESOLVED", "rationale": "t"}]}
    (root / "datasets" / "config" / "class_mapping.json").write_text(json.dumps(mapping))
    return files


def run(root: Path, script: str, *args):
    env = {**os.environ, "VISIONTRA_ROOT": str(root)}
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], env=env, capture_output=True, text=True, timeout=300)


def read(root: Path, rel: str) -> list[dict]:
    with open(root / rel, newline="") as f:
        return list(csv.DictReader(f))


def raw_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((root / "datasets" / "raw").rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


@pytest.fixture()
def fx(tmp_path):
    build_fixture(tmp_path)
    return tmp_path


def pipeline(root: Path) -> list[int]:
    r = run(root, "run_prep.py")
    return r


def test_full_pipeline_detects_problems_and_preserves_raw(fx):
    before = raw_digest(fx)
    r = pipeline(fx)
    assert r.returncode == 1, r.stdout + r.stderr          # findings exist (discrepancies, invalid labels) but nothing failed
    assert "== done" in r.stdout
    assert raw_digest(fx) == before, "raw data must not change"
    rec = json.loads((fx / f"datasets/interim/{PREP}/reconciliation.json").read_text())["datasets"]["VT-DS-901"]["discrepancies"]
    assert rec["duplicate_manifest_file_ids"] and rec["duplicate_manifest_paths"]
    assert rec["label_files_without_image"] == ["train/labels/orphan.txt"]
    assert rec["images_without_label_file"] == ["train/images/nolabel.jpg"]
    assert rec["unreadable_images"] == ["train/images/trunc.jpg"]
    assert rec["extension_format_mismatch"] == ["train/images/fakeext.jpg"]
    # exact duplicates with conflicting annotations
    ex = json.loads((fx / f"datasets/interim/{PREP}/exact_duplicates.json").read_text())
    assert ex["duplicate_groups"] == 1 and ex["annotation_conflict_groups"] == 1
    assert ex["groups"][0]["canonical_file_id"] is None
    # near duplicates
    pairs = {frozenset((p["file_a"].split("/")[-1], p["file_b"].split("/")[-1])): p["category"]
             for p in read(fx, f"datasets/interim/{PREP}/near_duplicate_pairs.csv")}
    assert pairs[frozenset(("q1.jpg", "q2.jpg"))] == "NEAR_IDENTICAL_CANDIDATE"
    assert pairs[frozenset(("r1.jpg", "r2.jpg"))] == "TRANSFORMED_COPY_CANDIDATE"
    assert pairs[frozenset(("dupx.jpg", "dupy.jpg"))] == "EXACT_DUPLICATE"
    assert sum(1 for c in pairs.values() if c != "SIMILAR_UNRESOLVED") == 3, "distinct textures must not be grouped"
    # annotations
    st = {r["file_id"].split("/")[-1]: r for r in read(fx, f"datasets/interim/{PREP}/annotation_status.csv")}
    assert st["badcls.jpg"]["annotation_status"] == "INVALID" and "CLASS_ID_OUT_OF_RANGE" in st["badcls.jpg"]["issue_codes"]
    assert st["nan.jpg"]["annotation_status"] == "INVALID" and "NON_FINITE" in st["nan.jpg"]["issue_codes"]
    assert st["fields.jpg"]["annotation_status"] == "INVALID"
    assert st["out.jpg"]["annotation_status"] == "REQUIRES_REVIEW"
    assert st["dupline.jpg"]["annotation_status"] == "VALID_WITH_WARNINGS"
    assert st["trunc.jpg"]["annotation_status"] == "BLOCKED"
    assert st["empty.jpg"]["annotation_status"] == "UNLABELLED" and st["nolabel.jpg"]["annotation_status"] == "UNLABELLED"
    prop = read(fx, f"datasets/annotations/{PREP}/proposed_corrections.csv")
    assert len(prop) == 1 and prop[0]["status"] == "PROPOSED_NOT_APPLIED"
    assert (fx / "datasets/raw/VT-DS-901/train/labels/dupline.txt").read_text().count("\n") == 2, "label file must stay unchanged"
    # derived manifest: stable ids, inclusion, unlabelled images never become negatives
    der = {r["file_id"]: r for r in read(fx, f"datasets/processed/{PREP}/derived_manifest.csv")}
    assert "VT-DS-901:train/images/q1.jpg" in der
    inc = {k.split("/")[-1]: (v["inclusion_status"], v["inclusion_reasons"]) for k, v in der.items()}
    assert inc["trunc.jpg"] == ("EXCLUDED", "UNREADABLE_IMAGE")
    assert inc["badcls.jpg"][0] == "EXCLUDED" and inc["nan.jpg"][0] == "EXCLUDED"
    assert inc["dupx.jpg"] == ("QUARANTINED", "EXACT_DUPLICATE_ANNOTATION_CONFLICT") == inc["dupy.jpg"]
    assert inc["empty.jpg"] == ("QUARANTINED", "UNLABELLED:PENDING_REVIEW")
    assert inc["nolabel.jpg"] == ("QUARANTINED", "UNLABELLED:PENDING_REVIEW")
    neg = read(fx, f"datasets/processed/{PREP}/negative_inventory.csv")
    assert {n["category"] for n in neg} == {"UNCERTAIN_DEFECT_STATUS"}, "no image may become a verified negative without review"
    # grouping keeps related images in one split
    split = {k.split("/")[-1]: v["assigned_split"] for k, v in der.items()}
    assert split["q1.jpg"] == split["q2.jpg"] and split["r1.jpg"] == split["r2.jpg"] and split["dupx.jpg"] == split["dupy.jpg"]
    assert split[f"a_jpg.rf.{H1}.jpg"] == split[f"a_jpg.rf.{H2}.jpg"]
    seq = {k.split("/")[-1]: v for k, v in der.items() if v["dataset_id"] == "VT-DS-902"}
    by_seq = {}
    for v in seq.values():
        by_seq.setdefault(v["video_sequence"], set()).add(v["assigned_split"])
    assert all(len(s) == 1 for s in by_seq.values()), "a video sequence must stay in one split"
    # quarantined images are not offered for use
    listed = {x["file_id"].split("/")[-1] for s in ("train", "valid", "test") for x in read(fx, f"datasets/splits/{PREP}/{s}.csv")}
    assert not ({"dupx.jpg", "dupy.jpg", "empty.jpg", "nolabel.jpg", "trunc.jpg", "badcls.jpg"} & listed)
    v = json.loads((fx / f"datasets/reports/{PREP}/split_validation.json").read_text())
    assert v["errors"] == 0
    assert any("no grouping evidence" in w for w in v["warnings"]), "images with unknown grouping must be reported"
    assert any("one aircraft" in w for w in v["warnings"])


def test_reproducible_with_fixed_configuration(fx):
    pipeline(fx)
    d1 = json.loads((fx / f"datasets/splits/{PREP}/split_summary.json").read_text())["assignment_sha256"]
    m1 = (fx / f"datasets/processed/{PREP}/derived_manifest.csv").read_bytes()
    p1 = (fx / f"datasets/interim/{PREP}/near_duplicate_pairs.csv").read_bytes()
    assert run(fx, "run_prep.py", "--from", "exact").returncode == 1
    assert json.loads((fx / f"datasets/splits/{PREP}/split_summary.json").read_text())["assignment_sha256"] == d1
    assert (fx / f"datasets/processed/{PREP}/derived_manifest.csv").read_bytes() == m1
    assert (fx / f"datasets/interim/{PREP}/near_duplicate_pairs.csv").read_bytes() == p1


def test_validate_splits_detects_leakage(fx):
    pipeline(fx)
    path = fx / f"datasets/processed/{PREP}/derived_manifest.csv"
    rows = read(fx, f"datasets/processed/{PREP}/derived_manifest.csv")
    for r in rows:
        if r["file_id"].endswith("q2.jpg"):
            r["assigned_split"] = "test" if r["assigned_split"] != "test" else "train"
        if r["file_id"].endswith("dupy.jpg"):
            r["assigned_split"] = "test" if r["assigned_split"] != "test" else "train"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    v = run(fx, "validate_splits.py")
    assert v.returncode == 1
    assert "NEAR_IDENTICAL_CANDIDATE pair" in v.stdout and "identical bytes" in v.stdout and "photo_group" in v.stdout
    assert "assignment digest" in v.stdout


def test_cross_split_similarity_warning(fx):
    pipeline(fx)
    der = read(fx, f"datasets/processed/{PREP}/derived_manifest.csv")
    a = next(r for r in der if r["assigned_split"] == "train" and r["inclusion_status"] == "RETAINED")
    b = next(r for r in der if r["assigned_split"] == "test" and r["inclusion_status"] == "RETAINED")
    pp = fx / f"datasets/interim/{PREP}/near_duplicate_pairs.csv"
    rows = read(fx, f"datasets/interim/{PREP}/near_duplicate_pairs.csv")
    rows.append({**rows[0], "pair_id": "NDP-999999", "file_a": a["file_id"], "file_b": b["file_id"], "asset_a": a["asset_id"],
                 "asset_b": b["asset_id"], "category": "SIMILAR_UNRESOLVED", "relation_hint": "no known relation"})
    with open(pp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    v = run(fx, "validate_splits.py")
    assert v.returncode == 0, v.stdout
    assert "SIMILAR_UNRESOLVED pairs cross splits" in v.stdout
    warn = read(fx, f"datasets/reports/{PREP}/cross_split_similarity_warnings.csv")
    assert any(w["pair_id"] == "NDP-999999" for w in warn)


def test_class_mapping_must_cover_every_source_class(fx):
    pipeline(fx)
    p = fx / "datasets/config/class_mapping.json"
    m = json.loads(p.read_text())
    m["entries"] = [e for e in m["entries"] if e["source_name"] != "dent"]
    m["entries"].append({"dataset": "VT-DS-902", "source_class_id": 0, "source_name": "Rust", "target": "corrosion", "status": "NOT_MAPPED"})
    p.write_text(json.dumps(m))
    r = run(fx, "validate_class_mapping.py")
    assert r.returncode == 1
    assert "'dent' is not in the mapping table" in r.stdout and "listed 2 times" in r.stdout and "must not name a target" in r.stdout


def test_confirmed_negative_requires_reviewer_and_evidence(fx):
    pipeline(fx)
    p = fx / "datasets/review/unlabelled_VT-DS-901.csv"
    rows = read(fx, "datasets/review/unlabelled_VT-DS-901.csv")
    rows[0]["review_status"] = "CONFIRMED_NEGATIVE"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    r = run(fx, "build_review_manifest.py")
    assert r.returncode == 2 and "CONFIRMED_NEGATIVE without reviewer and evidence" in r.stderr
    rows[0].update(reviewer="Inspector A (test)", evidence="test record")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert run(fx, "build_review_manifest.py").returncode == 0
    assert run(fx, "create_grouped_splits.py").returncode == 0
    neg = {n["file_id"]: n["category"] for n in read(fx, f"datasets/processed/{PREP}/negative_inventory.csv")}
    assert neg[rows[0]["file_id"]] == "VERIFIED_NORMAL"


def test_missing_input_gives_actionable_error(fx):
    r = run(fx, "find_near_duplicates.py")
    assert r.returncode == 2 and ("run inventory_dataset.py first" in r.stderr or "run the previous step first" in r.stderr)


def test_outputs_cannot_be_written_into_raw(fx, monkeypatch):
    monkeypatch.setenv("VISIONTRA_ROOT", str(fx))
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import prep_common
    importlib.reload(prep_common)
    with pytest.raises(SystemExit):
        prep_common.write_text(fx / "datasets" / "raw" / "VT-DS-901" / "x.txt", "no")
    assert not (fx / "datasets" / "raw" / "VT-DS-901" / "x.txt").exists()


def test_report_agrees_with_manifests(fx):
    pipeline(fx)
    assert run(fx, "generate_dataset_quality_report.py", "--check").returncode == 0
    rp = fx / f"datasets/reports/{PREP}/data_quality_report.json"
    rep = json.loads(rp.read_text())
    rep["combined"]["image_files"] += 1
    rp.write_text(json.dumps(rep))
    r = run(fx, "generate_dataset_quality_report.py", "--check")
    assert r.returncode == 1 and "differs from a fresh recomputation" in r.stdout
