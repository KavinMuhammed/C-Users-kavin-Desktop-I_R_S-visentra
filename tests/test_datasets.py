"""Tests for the Phase 3 dataset tooling (scripts/datasets/*) using a fake source served locally.

Covers: licence gate, download + reuse (no repeat download), git-blob verification, unsafe archive
members rejected (path traversal, symlink), no-overwrite rule, manifest build from real files,
augmentation grouping, validator pass/fail cases. No network access beyond 127.0.0.1.
"""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import threading
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts" / "datasets"


def png(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x80" * (w * 3) for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "root"
    (root / "datasets" / "metadata").mkdir(parents=True)
    (root / ".gitignore").write_text("datasets/raw/\ndatasets/archives/\n")
    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()
    h = "0123456789abcdef0123456789abcdef"
    files = {
        "ds/data.yaml": b"nc: 2\nnames: ['crack', 'dent']\n",
        f"ds/train/images/a_jpg.rf.{h}.png": png(8, 6),
        f"ds/train/labels/a_jpg.rf.{h}.txt": b"0 0.5 0.5 0.2 0.2\n1 0.3 0.3 0.1 0.1\n",
        f"ds/train/images/a_jpg.rf.{h[::-1]}.png": png(8, 6),
        f"ds/train/labels/a_jpg.rf.{h[::-1]}.txt": b"0 0.5 0.5 0.2 0.2\n",
        "ds/test/images/b.png": png(10, 4),
        "ds/test/labels/b.txt": b"",
        "code/train.py": b"print('not extracted')\n",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p, d in files.items():
            ti = tarfile.TarInfo("repo-abc/" + p); ti.size = len(d); tf.addfile(ti, io.BytesIO(d))
        evil = b"pwned"
        ti = tarfile.TarInfo("repo-abc/../evil.txt"); ti.size = len(evil); tf.addfile(ti, io.BytesIO(evil))
        ln = tarfile.TarInfo("repo-abc/ds/train/images/link.png"); ln.type = tarfile.SYMTYPE; ln.linkname = "/etc/passwd"; tf.addfile(ln)
    (srv_dir / "archive.tar.gz").write_bytes(buf.getvalue())
    tree = {"truncated": False, "tree": [{"path": p, "type": "blob", "sha": blob(d)} for p, d in files.items()]}
    (srv_dir / "tree.json").write_text(json.dumps(tree))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(srv_dir), **k)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    cand = {"candidates": [
        {"id": "VT-DS-901", "name": "Fake OK", "landing_url": "http://example.invalid", "decision": "SELECTED", "status": "DOWNLOAD_PENDING",
         "class_mapping": {"crack": "crack", "dent": "dent"}, "licence": {"name": "CC BY 4.0", "status": "VERIFIED", "evidence": ["test"], "commercial_use": "permitted"}},
        {"id": "VT-DS-902", "name": "Fake unresolved", "landing_url": "http://example.invalid", "decision": "DEFERRED", "status": "LICENCE_REVIEWED",
         "licence": {"name": "?", "status": "UNRESOLVED", "evidence": []}},
    ], "acquisitions": [
        {"id": "VT-ACQ-901", "method": "github_archive", "url": f"http://127.0.0.1:{port}/archive.tar.gz", "tree_url": f"http://127.0.0.1:{port}/tree.json",
         "archive_name": "fake.tar.gz", "extract": [{"dataset": "VT-DS-901", "prefix": "ds/", "include_ext": [".png", ".txt", ".yaml"]}]},
        {"id": "VT-ACQ-902", "method": "github_archive", "url": f"http://127.0.0.1:{port}/archive.tar.gz", "tree_url": f"http://127.0.0.1:{port}/tree.json",
         "archive_name": "fake2.tar.gz", "extract": [{"dataset": "VT-DS-902", "prefix": "ds/", "include_ext": [".png"]}]},
    ]}
    (root / "datasets" / "metadata" / "candidates.json").write_text(json.dumps(cand))
    yield root
    httpd.shutdown()


def run(root: Path, script: str, *args):
    e = {**os.environ, "VISIONTRA_ROOT": str(root), "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], env=e, capture_output=True, text=True, timeout=120)


def log_events(root):
    return [json.loads(l) for l in (root / "datasets" / "logs" / "acquisition.jsonl").read_text().splitlines()]


def test_acquire_verify_extract_and_manifest(env):
    r = run(env, "acquire.py", "VT-ACQ-901")
    assert r.returncode == 0, r.stdout + r.stderr
    raw = env / "datasets" / "raw" / "VT-DS-901"
    assert (raw / "data.yaml").exists() and (raw / "test" / "images" / "b.png").exists()
    assert not (env / "datasets" / "evil.txt").exists() and not (env / "evil.txt").exists(), "path traversal must be rejected"
    assert not (raw / "train" / "images" / "link.png").exists(), "symlink must be rejected"
    assert not list(raw.rglob("train.py")), "files outside the prefix must not be extracted"
    ev = log_events(env)
    res = [e for e in ev if e["event"] == "extract_result"][-1]
    assert res["complete"] and res["verified"] == 7 and res["mismatch"] == 0
    assert sum(1 for e in ev if e["event"] == "member_rejected") == 2
    assert (env / "datasets" / "archives" / "fake.tar.gz.sha256").exists()
    # second run reuses the archive and skips identical files
    r2 = run(env, "acquire.py", "VT-ACQ-901")
    assert r2.returncode == 0
    ev = log_events(env)
    assert any(e["event"] == "archive_reused" for e in ev)
    assert [e for e in ev if e["event"] == "extract_result"][-1]["skipped_identical"] == 7
    # manifest from real files
    assert run(env, "build_manifest.py").returncode == 0
    m = json.loads((env / "datasets" / "manifests" / "datasets.json").read_text())
    d = {x["id"]: x for x in m["datasets"]}
    ok = d["VT-DS-901"]
    assert ok["status"] == "INTEGRITY_VERIFIED"
    meas = ok["measured"]
    assert meas["images"] == 3 and meas["unique_source_images"] == 2, "augmented copies share one source"
    assert meas["instances_by_class"] == {"crack": 2, "dent": 1}
    assert meas["images_containing_class"] == {"crack": 2, "dent": 1}
    assert meas["unique_source_images_containing_class"] == {"crack": 1, "dent": 1}
    assert meas["images_with_empty_label_file"] == 1
    assert d["VT-DS-902"]["status"] == "LICENCE_REVIEWED" and d["VT-DS-902"]["measured"] is None
    v = run(env, "validate_manifest.py", "--full")
    assert v.returncode == 0, v.stdout


def test_refuses_unverified_licence(env):
    r = run(env, "acquire.py", "VT-ACQ-902")
    assert r.returncode != 0 and "licence" in (r.stdout + r.stderr)
    assert not (env / "datasets" / "raw" / "VT-DS-902").exists()


def test_refuses_overwrite_of_different_file(env):
    raw = env / "datasets" / "raw" / "VT-DS-901"
    raw.mkdir(parents=True)
    (raw / "data.yaml").write_text("user edited\n")
    r = run(env, "acquire.py", "VT-ACQ-901")
    assert r.returncode != 0 and "refusing to overwrite" in (r.stdout + r.stderr)
    assert (raw / "data.yaml").read_text() == "user edited\n"


def test_validator_detects_tampering_and_false_status(env):
    assert run(env, "acquire.py", "VT-ACQ-901").returncode == 0
    assert run(env, "build_manifest.py").returncode == 0
    f = env / "datasets" / "raw" / "VT-DS-901" / "test" / "images" / "b.png"
    f.write_bytes(f.read_bytes()[:-1] + b"\x00")  # same size, different content
    v = run(env, "validate_manifest.py", "--full")
    assert v.returncode == 1 and "sha256 differs" in v.stdout
    mp = env / "datasets" / "manifests" / "datasets.json"
    m = json.loads(mp.read_text())
    for d in m["datasets"]:
        if d["id"] == "VT-DS-902":
            d["status"] = "DOWNLOADED"
    mp.write_text(json.dumps(m))
    v = run(env, "validate_manifest.py")
    assert v.returncode == 1 and "VT-DS-902: status DOWNLOADED but no files" in v.stdout


def test_verify_only_and_fast_resume(env):
    assert run(env, "acquire.py", "VT-ACQ-901", "--max-seconds", "0.000001").returncode == 3  # pauses immediately
    ev = log_events(env)
    assert any(e["event"] == "extract_paused" for e in ev)
    assert run(env, "acquire.py", "VT-ACQ-901", "--fast-resume").returncode == 0
    v = run(env, "acquire.py", "VT-ACQ-901", "--verify-only")
    assert v.returncode == 0
    last = [e for e in log_events(env) if e["event"] == "extract_result"][-1]
    assert last["mode"] == "verify_only" and last["complete"] and last["verified"] == 7
    f = env / "datasets" / "raw" / "VT-DS-901" / "data.yaml"
    f.write_bytes(f.read_bytes().replace(b"crack", b"CRACK"))  # same size, different content
    v = run(env, "acquire.py", "VT-ACQ-901", "--verify-only")
    assert v.returncode == 2
    last = [e for e in log_events(env) if e["event"] == "extract_result"][-1]
    assert last["mismatch"] == 1 and not last["complete"]


def test_sharded_verification(env):
    assert run(env, "acquire.py", "VT-ACQ-901").returncode == 0
    for k in range(3):
        assert run(env, "acquire.py", "VT-ACQ-901", "--verify-only", "--shard", f"{k}/3").returncode == 0
    assert run(env, "acquire.py", "VT-ACQ-901", "--verify-only", "--aggregate", "3").returncode == 0
    last = [e for e in log_events(env) if e["event"] == "extract_result"][-1]
    assert last["shards"] == 3 and last["complete"] and last["verified"] == 7
    assert run(env, "acquire.py", "VT-ACQ-901", "--verify-only", "--aggregate", "4").returncode != 0  # missing shards


def test_manifest_build_is_resumable(env):
    assert run(env, "acquire.py", "VT-ACQ-901").returncode == 0
    r = run(env, "build_manifest.py", "--max-seconds", "0.000001")
    assert r.returncode == 3 and "PAUSED" in r.stdout
    assert run(env, "build_manifest.py").returncode == 0
    assert run(env, "validate_manifest.py", "--full").returncode == 0
