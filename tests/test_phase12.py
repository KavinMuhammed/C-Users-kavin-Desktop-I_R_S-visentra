"""Phase 12 tests: server analytics (EP-31), batched review folding (P-01/P-02), read-only dataset API (EP-20…25, 45–48),
SAMPLE-record policy, compressed frontend delivery.

Dataset tests read the repository's real dataset files (datasets/raw, VT-PREP-001). They are skipped where those files
are absent (e.g. a checkout without the downloaded data). AI predictions are TEST FIXTURE rows (tests/p11_fixtures.py).
"""
from __future__ import annotations

import gzip
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from conftest import Api
from p11_fixtures import URL, add_fixture_predictions, create, decide, finding, signoff, user
from visentra_api import detection_data as DDS
from visentra_api.db import SessionLocal
from visentra_api.inspection import review as RV
from visentra_api.main import app
from visentra_api.models import Inspection
from visentra_api.security import limiter

HAVE_DATA = (DDS.root() / "processed" / DDS.PREP_ID / "derived_manifest.csv").exists() and (DDS.root() / "raw" / "VT-DS-001").is_dir()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="repository dataset files (datasets/raw, VT-PREP-001) are not present")
V = f"/api/v1/detection-dataset-versions/{DDS.PREP_ID}"


@pytest.fixture()
def a():
    limiter.reset()
    with TestClient(app) as c:
        x = Api(c)
        x.me = user(x, "p12")
        yield x


def other() -> Api:
    c = TestClient(app)
    c.__enter__()
    x = Api(c)
    x.me = user(x, "p12o")
    return x


# ----------------------------------------------------------------------------------------- batched folding
def test_batched_review_state_equals_the_per_inspection_fold(a):
    ids = []
    for k in range(4):
        insp = create(a)
        ids.append(insp["id"])
        if k == 0:                                   # reviewer findings: one rejected, one deleted
            f1 = finding(a, insp, "dent").json()["finding"]["id"]
            f2 = finding(a, insp, "crack").json()["finding"]["id"]
            decide(a, insp["id"], "human_finding", f1, "rejected")
            decide(a, insp["id"], "human_finding", f2, None, kind="delete")
        if k == 1:                                   # TEST FIXTURE predictions: confirm, reclassify, leave pending
            p = add_fixture_predictions(insp["id"], [("dent", .9, (.1, .1, .2, .2)), ("crack", .4, (.5, .5, .1, .1)), ("dent", .3, (.3, .3, .1, .1))])
            decide(a, insp["id"], "prediction", p[0], "confirmed")
            decide(a, insp["id"], "prediction", p[1], None, kind="reclassify", corrected_class_id="corrosion")
        if k == 2:
            finding(a, insp, "corrosion")
            assert signoff(a, insp["id"]).status_code == 200
    with SessionLocal() as db:
        rows = list(db.scalars(select(Inspection).where(Inspection.id.in_(ids))))
        batch = RV.states_batch(db, rows)
        for i in rows:
            assert RV.counts_and_categories(batch[i.id]) == RV.counts_and_categories(RV.state(db, i)), i.ref


def test_category_filter_is_scoped_to_the_callers_workspaces(a):
    mine = create(a)
    finding(a, mine, "corrosion")
    b = other()
    theirs = create(b)
    finding(b, theirs, "corrosion")
    got = a.get(f"{URL}?category=corrosion").json()
    assert [x["id"] for x in got["items"]] == [mine["id"]]
    with SessionLocal() as db:
        ws = db.get(Inspection, mine["id"]).workspace_id
        assert RV.category_inspection_ids(db, "corrosion", [ws]) == {mine["id"]}
        assert RV.category_inspection_ids(db, "corrosion", []) == set()


# ----------------------------------------------------------------------------------------- EP-31 analytics
def test_analytics_counts_saved_records_only_and_respects_workspaces(a):
    anon = TestClient(app)
    assert anon.get("/api/v1/inspection-analytics").status_code == 401
    empty = a.get("/api/v1/inspection-analytics").json()
    assert empty["counts"]["inspections"] == 0 and empty["scope"]["includes_sample_data"] is False and empty["attention"] == []
    i1, i2, i3 = create(a), create(a), create(a)
    finding(a, i1, "dent")
    f = finding(a, i2, "crack").json()["finding"]["id"]
    decide(a, i2["id"], "human_finding", f, "rejected")
    assert signoff(a, i2["id"]).status_code == 200
    p = add_fixture_predictions(i3["id"], [("dent", .8, (.1, .1, .2, .2))])          # pending AI prediction
    gone = create(a)
    assert a.delete(f"{URL}/{gone['id']}", headers={"If-Match": '"0"'}).status_code in (200, 204)
    b = other()
    create(b)                                                                         # someone else's record
    r = a.get("/api/v1/inspection-analytics").json()
    assert r["counts"] == {"inspections": 3, "awaiting_review": 1, "in_review": 1, "reviewed": 1, "analysing": 0}
    assert r["findings"]["total"] == 3 and r["findings"]["ai_predictions"] == 1 and r["findings"]["reviewer_findings"] == 2
    assert r["findings"]["by_decision"] == {"pending": 1, "confirmed": 1, "rejected": 1, "further_review": 0}
    assert r["findings"]["confirmed_by_category"] == {"dent": 1}
    assert r["attention"][0]["id"] == i3["id"] and r["attention"][0]["pending"] == 1 and {x["id"] for x in r["attention"]} == {i1["id"], i3["id"]}
    assert sum(d["created"] for d in r["timeline"]) == 3 and len(r["timeline"]) == 30
    assert set(r["definitions"]) >= {"inspections", "findings", "reports", "timeline"}
    assert p and "accuracy" not in str(r).lower()


# ----------------------------------------------------------------------------------------- capabilities and SAMPLE policy
def test_capabilities_report_datasets_analytics_and_sample_policy(a):
    i = a.get("/api/system/capabilities").json()["inspection"]
    assert i["detection_datasets"] is True and i["analytics"] is True and i["detection_model"]["available"] is False
    assert i["demo_samples"] == {"allowed": False}   # Phase 13: off by default everywhere; the UI no longer contains SAMPLE records


def test_sample_records_are_never_allowed_in_production(monkeypatch):
    from visentra_api import config
    monkeypatch.setenv("VISENTRA_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "p" * 48)
    monkeypatch.setenv("VISIONTRA_DEMO_SAMPLES", "1")
    assert config.load_settings().demo_samples_allowed is False
    monkeypatch.setenv("VISENTRA_ENV", "development")
    assert config.load_settings().demo_samples_allowed is True
    monkeypatch.setenv("VISIONTRA_DEMO_SAMPLES", "0")
    assert config.load_settings().demo_samples_allowed is False


# ----------------------------------------------------------------------------------------- dataset API
def test_dataset_api_requires_a_signed_in_reader():
    anon = TestClient(app)
    for u in ("/api/v1/detection-datasets", "/api/v1/class-mappings", "/api/v1/dataset-sources", V, f"{V}/images", f"{V}/images/{'a' * 24}/thumb"):
        assert anon.get(u).status_code == 401, u


@needs_data
def test_registry_mapping_and_version_come_from_the_repository_files(a):
    d = a.get("/api/v1/detection-datasets").json()
    assert d["total"] == 16 and d["by_status"]["INTEGRITY_VERIFIED"] == 2
    ds1 = next(x for x in d["items"] if x["id"] == "VT-DS-001")
    assert ds1["licence"]["name"] == "CC BY 4.0" and ds1["images_viewable"] is True and ds1["measured"]["images"] == 10722
    restricted = [x for x in d["items"] if x["status"] in ("RESTRICTED", "EXCLUDED", "DOWNLOAD_PENDING")]
    assert restricted and not any(x["images_viewable"] for x in restricted)
    m = a.get("/api/v1/class-mappings?dataset=VT-DS-001").json()
    st = {e["source_name"]: e["status"] for e in m["items"]}
    assert st["dent"] == "PROVISIONAL_ACCEPTED" and st["crack"] == "PROVISIONAL_ACCEPTED" and st["scratch"] == "NOT_MAPPED"   # CMAP-001 v2
    assert a.get("/api/v1/class-mappings?dataset=VT-DS-999").status_code == 422
    v = a.get(V).json()
    assert v["files"] == 11705 and v["by_split"] == {"train": 8468, "valid": 2175, "test": 1062}
    assert "NOT an aircraft-independent evaluation" in v["evaluation_scope"]
    assert a.get("/api/v1/detection-dataset-versions/VT-PREP-999").status_code == 404
    assert a.get("/api/v1/detection-datasets/VT-DS-001").json()["licence"]["status"] == "VERIFIED"


@needs_data
def test_image_list_is_paged_filtered_and_never_returns_paths(a):
    r = a.get(f"{V}/images?dataset=VT-DS-001&split=train&per_page=10&page=2").json()
    assert r["total"] > 7000 and len(r["items"]) == 10 and r["page"] == 2 and r["facets"]["split"] == {"train": r["total"]}
    it = r["items"][0]
    assert it["viewable"] and it["thumb_url"].startswith(V) and "rel_path" not in it and "datasets/raw" not in str(r)
    q = a.get(f"{V}/images?inclusion_status=QUARANTINED&per_page=120").json()
    assert q["total"] == 215 and all(x["inclusion_status"] == "QUARANTINED" and x["inclusion_reasons"] for x in q["items"])
    u = a.get(f"{V}/images?annotation_status=UNLABELLED&dataset=VT-DS-001&per_page=120").json()
    assert u["total"] == 56 and all(x["n_objects"] == 0 for x in u["items"])
    for bad in ("per_page=0", "per_page=121", "page=0"):
        assert a.get(f"{V}/images?{bad}").status_code == 422, bad


@needs_data
def test_held_out_test_images_are_listed_but_never_displayed(a):
    t = a.get(f"{V}/images?split=test&per_page=5").json()
    assert t["total"] == 1062 and all(not x["viewable"] and "held-out" in x["not_viewable_reason"] and "thumb_url" not in x for x in t["items"])
    iid = t["items"][0]["image_id"]
    r = a.get(f"{V}/images/{iid}/thumb")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    d = a.get(f"{V}/images/{iid}").json()
    assert d["viewable"] is False and "boxes" not in d


@needs_data
def test_preview_is_a_metadata_free_jpeg_cached_by_checksum(a):
    it = a.get(f"{V}/images?dataset=VT-DS-001&split=train&per_page=1").json()["items"][0]
    r = a.get(it["thumb_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"].startswith("private")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    im = Image.open(io.BytesIO(r.content))
    assert max(im.size) <= 320 and not im.getexif() and "icc_profile" not in im.info
    assert a.get(it["thumb_url"], headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    big = a.get(it["thumb_url"] + "?size=preview")
    assert big.status_code == 200 and max(Image.open(io.BytesIO(big.content)).size) <= 1280
    assert a.get(it["thumb_url"] + "?size=huge").status_code == 422
    cached = list((DDS.Path(DDS.settings.data_dir) / "cache" / "dataset-previews").glob(f"{it['sha256']}-320-*.jpg"))
    assert len(cached) == 1


@needs_data
def test_image_detail_shows_source_labels_and_mapping_without_guessing(a):
    rows = a.get(f"{V}/images?dataset=VT-DS-001&split=train&source_class=crack&per_page=20").json()["items"]
    d = a.get(f"{V}/images/{rows[0]['image_id']}?verify=true").json()
    assert d["integrity"] == "ok" and d["width"] > 0 and d["boxes"]
    for b in d["boxes"]:
        assert 0 <= b["box"]["x"] <= 1 and 0 < b["box"]["w"] <= 1 and not b["out_of_bounds"]
        if b["source_class"] == "crack":
            assert b["mapping_status"] == "PROVISIONAL_ACCEPTED" and b["target_class"] == "crack"   # CMAP-001 v2 product-owner decision
        if b["source_class"] in ("scratch", "paint-off", "missing-head"):
            assert b["mapping_status"] == "NOT_MAPPED" and b["target_class"] is None             # never guessed
        if b["source_class"] == "dent":
            assert b["target_class"] == "dent" and b["mapping_status"] == "PROVISIONAL_ACCEPTED"


@pytest.mark.parametrize("image_id", ["../../../etc/passwd", "a" * 24, "%2e%2e%2f" * 4, "x" * 200])
def test_unknown_or_hostile_image_ids_are_not_found(a, image_id):
    assert a.get(f"{V}/images/{image_id}").status_code in (404, 422)
    assert a.get(f"{V}/images/{image_id}/thumb").status_code in (404, 422)


def test_manifest_paths_cannot_escape_the_raw_directory():
    with pytest.raises(Exception) as ex:
        DDS._safe_path("datasets/raw/../../apps/api/visentra_api/config.py")
    assert getattr(ex.value, "status_code", None) == 404


@needs_data
def test_readiness_refuses_unresolved_classes_and_passes_dent_structurally(a):
    # CMAP-001 v2: VT-DS-001 has no mapping to corrosion, so corrosion cannot be trained from it alone
    r = a.get(f"{V}/readiness?classes=corrosion&datasets=VT-DS-001").json()
    assert r["decision"] == "REFUSED" and "class_mapping_approved" in r["blocking"]
    assert "Do not train these classes" in r["conditions"]["class_mapping_approved"]["detail"]
    r = a.get(f"{V}/readiness?classes=crack,dent,corrosion&datasets=VT-DS-001,VT-DS-002").json()
    assert r["decision"] == "PASSED_STRUCTURAL_CHECKS"                      # the VT-EXP-004 training request
    for bad in ("classes=paint&datasets=VT-DS-001", "classes=dent&datasets=VT-DS-009", "classes=&datasets=VT-DS-001"):
        assert a.get(f"{V}/readiness?{bad}").status_code == 422, bad
    t0 = time.time()
    ok = a.get(f"{V}/readiness?classes=dent&datasets=VT-DS-001").json()
    assert ok["decision"] == "PASSED_STRUCTURAL_CHECKS" and ok["structural_checks_passed"] is True
    assert ok["conditions"]["no_file_or_group_crosses_splits"]["ok"] is True and ok["conditions"]["files_readable"]["ok"] is None
    assert ok["conditions"]["verified_negative_images"]["ok"] is False and "dataset_licences_verified" in ok["conditions"]
    assert ok["splits"]["test"]["images"] == 331 and "validate_training_config.py" in ok["next_step"]
    t1 = time.time()
    a.get(f"{V}/readiness?classes=dent&datasets=VT-DS-001")
    assert time.time() - t1 < max(1.0, (t1 - t0) / 5)                                # cached until the files change


# ----------------------------------------------------------------------------------------- frontend delivery
def test_frontend_is_compressed_and_revalidated(a):
    r = a.get("/", headers={"Accept-Encoding": "gzip"})
    if r.status_code == 503:
        pytest.skip("frontend not built")
    assert r.status_code == 200 and r.headers.get("etag") and r.headers["cache-control"] == "no-cache"
    raw = a.get("/", headers={"Accept-Encoding": "identity"})
    assert raw.headers.get("content-encoding") is None and raw.content[:15].lower() == b"<!doctype html>"
    assert r.content == raw.content                                                   # TestClient decodes gzip; content identical
    assert len(gzip.compress(raw.content)) < len(raw.content) / 2
    assert a.get("/", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert a.get("/inspection/whatever", headers={"Accept-Encoding": "gzip"}).headers.get("etag") == r.headers["etag"]


# ----------------------------------------------------------------------------------------- failure behaviour
def test_missing_dataset_registry_is_reported_not_invented(a, tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONTRA_DATASETS_ROOT", str(tmp_path))
    for u in ("/api/v1/detection-datasets", V, f"{V}/images"):
        r = a.get(u)
        assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_UNAVAILABLE", u


@needs_data
def test_a_missing_image_file_is_reported_and_never_served(a, tmp_path, monkeypatch):
    import shutil
    src = DDS.root()
    rows = a.get(f"{V}/images?dataset=VT-DS-001&split=train&per_page=1&page=7").json()["items"]
    target = rows[0]
    assert a.get(target["thumb_url"] + "?size=preview").status_code == 200              # preview now cached for this file
    for rel in ("manifests/datasets.json", "config/class_mapping.json", f"processed/{DDS.PREP_ID}/derived_manifest.csv", "raw/VT-DS-001/data.yaml"):
        (tmp_path / "datasets" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / rel, tmp_path / "datasets" / rel)
    monkeypatch.setenv("VISIONTRA_DATASETS_ROOT", str(tmp_path / "datasets"))
    d = a.get(f"{V}/images/{target['image_id']}").json()
    assert d["integrity"] == "missing" and d["boxes"] == []
    r = a.get(f"{V}/images/{target['image_id']}/thumb?size=preview")
    assert r.status_code == 404 and r.json()["error"]["details"]["reason"] == "missing"     # the cached preview is not served


# ----------------------------------------------------------------------------------------- AT-35: injected dataset faults are found
def test_dataset_reverification_finds_injected_faults(tmp_path):
    """Duplicate file, corrupt image, out-of-bounds box, unknown class, missing label and a photo group in two splits."""
    import csv
    import hashlib
    import json
    import subprocess
    import sys
    from pathlib import Path
    REPO = Path(__file__).resolve().parents[1]
    raw = tmp_path / "datasets/raw/VT-DS-001"
    (raw / "train/images").mkdir(parents=True); (raw / "train/labels").mkdir(parents=True)
    (raw / "data.yaml").write_text("names: ['crack', 'dent']\n")
    def img(seed):
        b = io.BytesIO(); Image.new("RGB", (32, 32), (seed, 10, 10)).save(b, "JPEG"); return b.getvalue()
    files = {"a.jpg": (img(1), "1 0.5 0.5 0.2 0.2\n"), "dup.jpg": (img(1), "1 0.5 0.5 0.2 0.2\n"), "corrupt.jpg": (b"\xff\xd8\xff not a jpeg", "1 0.5 0.5 0.2 0.2\n"),
             "oob.jpg": (img(3), "1 0.95 0.5 0.3 0.2\n"), "badcls.jpg": (img(4), "7 0.5 0.5 0.2 0.2\n"), "nolabel.jpg": (img(5), None)}
    rows = []
    for k, (name, (data, label)) in enumerate(files.items()):
        (raw / "train/images" / name).write_bytes(data)
        if label is not None:
            (raw / "train/labels" / name.replace(".jpg", ".txt")).write_text(label)
        rows.append({"file_id": f"VT-DS-001:train/images/{name}", "dataset_id": "VT-DS-001", "rel_path": f"datasets/raw/VT-DS-001/train/images/{name}",
                     "sha256": hashlib.sha256(data).hexdigest(), "photo_group": "PG-1" if name in ("a.jpg", "oob.jpg") else f"PG-{k + 10}",
                     "assigned_split": "test" if name == "oob.jpg" else "train"})
    man = tmp_path / "datasets/processed/VT-PREP-001"; man.mkdir(parents=True)
    with (man / "derived_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    out = tmp_path / "report.json"
    p = subprocess.run([sys.executable, str(REPO / "scripts/datasets/verify_prepared_files.py"), "--root", str(tmp_path), "--out", str(out)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 1, p.stderr
    r = json.loads(out.read_text())
    c = r["counts"]
    assert c["corrupt_image"] == 1 and c["box_out_of_bounds"] == 1 and c["unknown_class_id"] == 1 and c["missing_label_file"] == 1
    assert r["exact_duplicate_files_by_sha256"] == 1 and r["photo_groups_in_more_than_one_split"] == 1
    assert any("corrupt.jpg" in x for x in r["examples"]["corrupt_image"])


@needs_data
def test_phase13_training_examples_show_real_labelled_train_images_only(a):
    d = a.get("/api/v1/training-examples?per_class=3").json()
    assert d["split"] == "train" and set(d["classes"]) == {"crack", "dent", "corrosion"}
    for cls, v in d["classes"].items():
        assert 1 <= len(v["items"]) <= 3, cls
        for it in v["items"]:
            assert it["boxes"] and all(0 <= b["x"] <= 1 and 0 < b["w"] <= 1 for b in it["boxes"])
            assert a.get(it["thumb_url"]).status_code == 200
    ids = {it["image_id"] for v in d["classes"].values() for it in v["items"]}
    test_rows = a.get(f"{V}/images?split=test&per_page=120").json()["items"]
    assert not ids & {r["image_id"] for r in test_rows}
