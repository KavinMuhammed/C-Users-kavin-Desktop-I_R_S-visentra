"""Phase 13: scripts/datasets/import_dataset.py on synthetic exports in a temporary copy of the dataset configuration.
The repository's datasets/ directory is never touched by these tests."""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def imp(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("import_dataset", REPO / "scripts/datasets/import_dataset.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ds = tmp_path / "datasets"
    (ds / "manifests" / "files").mkdir(parents=True)
    (ds / "config" / "prep").mkdir(parents=True)
    (ds / "raw").mkdir()
    shutil.copy(REPO / "datasets/manifests/datasets.json", ds / "manifests/datasets.json")
    shutil.copy(REPO / "datasets/config/class_mapping.json", ds / "config/class_mapping.json")
    shutil.copy(REPO / "datasets/config/prep/VT-PREP-001.json", ds / "config/prep/VT-PREP-001.json")
    for k, v in {"ROOT": tmp_path, "DS": ds, "REGISTRY": ds / "manifests/datasets.json", "MAPPING": ds / "config/class_mapping.json", "PREP_DIR": ds / "config/prep"}.items():
        monkeypatch.setattr(m, k, v)
    return m


def jpg(seed: int) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (seed * 7 % 255, 90, 120)).save(b, "JPEG")
    return b.getvalue()


def export(path: Path, extra: dict | None = None, wrap: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(wrap + "data.yaml", "train: ../train/images\nnc: 3\nnames: ['Crack', 'dent', 'scratch']\n")
        for split, n in (("train", 3), ("valid", 1)):
            for i in range(n):
                z.writestr(f"{wrap}{split}/images/img{i}_jpg.rf.abc{i}.jpg", jpg(i))
                z.writestr(f"{wrap}{split}/labels/img{i}_jpg.rf.abc{i}.txt", "0 0.5 0.5 0.2 0.2\n2 0.3 0.3 0.1 0.1\n")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


def test_import_records_the_acquisition_mapping_and_a_new_prep(imp, tmp_path):
    z = export(tmp_path / "e.zip", wrap="Aircraft-Surface-Damage.v3/")
    assert imp.main(["VT-DS-003", str(z)]) == 0
    raw = tmp_path / "datasets/raw/VT-DS-003"
    assert (raw / "data.yaml").is_file() and len(list((raw / "train/images").iterdir())) == 3
    reg = {d["id"]: d for d in json.loads((tmp_path / "datasets/manifests/datasets.json").read_text())["datasets"]}
    assert reg["VT-DS-003"]["status"] == "INTEGRITY_VERIFIED" and reg["VT-DS-003"]["acquisition"]["extract_result"]["images"] == 4
    m = json.loads((tmp_path / "datasets/config/class_mapping.json").read_text())
    new = {e["source_name"]: (e["target"], e["status"]) for e in m["entries"] if e["dataset"] == "VT-DS-003"}
    assert new == {"Crack": ("crack", "PROVISIONAL_ACCEPTED"), "dent": ("dent", "PROVISIONAL_ACCEPTED"), "scratch": (None, "NOT_MAPPED")}
    prep = json.loads((tmp_path / "datasets/config/prep/VT-PREP-002.json").read_text())
    assert prep["prep_id"] == "VT-PREP-002" and "VT-DS-003" in prep["datasets"]
    rows = (tmp_path / "datasets/manifests/files/VT-DS-003.csv").read_text().splitlines()
    assert len(rows) == 1 + 1 + 4 + 4
    assert imp.main(["VT-DS-003", str(z)]) == 1          # already acquired: refused


def test_dry_run_changes_nothing(imp, tmp_path):
    before = (tmp_path / "datasets/manifests/datasets.json").read_text()
    assert imp.main(["VT-DS-004", str(export(tmp_path / "e.zip")), "--dry-run"]) == 0
    assert (tmp_path / "datasets/manifests/datasets.json").read_text() == before
    assert not (tmp_path / "datasets/raw/VT-DS-004").exists()


@pytest.mark.parametrize("extra", [{"../evil.txt": "x"}, {"train/images/run.exe": "MZ"}])
def test_unsafe_archives_are_refused(imp, tmp_path, extra):
    assert imp.main(["VT-DS-005", str(export(tmp_path / "e.zip", extra))]) == 1
    assert not (tmp_path / "datasets/raw/VT-DS-005").exists() and not list((tmp_path / "datasets/raw").glob(".*staging"))


def test_excluded_or_unlicensed_datasets_are_refused(imp, tmp_path):
    z = export(tmp_path / "e.zip")
    assert imp.main(["VT-DS-009", str(z)]) == 1          # EXCLUDED
    assert imp.main(["VT-DS-001", str(z)]) == 1          # already acquired
    assert imp.main(["VT-DS-999", str(z)]) == 1          # not registered
