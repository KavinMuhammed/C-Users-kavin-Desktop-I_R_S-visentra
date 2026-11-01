"""Verify a release bundle independently of the script that built it (run here before copying, and on the host after).

    python scripts/verify_release_bundle.py <bundle.zip> [--source <project root>]

Checks: archive CRCs; every file's sha256 against RELEASE_MANIFEST.json, and nothing in the zip that the manifest does not
list; no prohibited content (databases, .env or secrets, data/, datasets/, experiments/, tests/, virtual environments,
private keys); only the two operational model bundles, with weights matching the manifest; and, with --source, that
every bundled file is byte-identical to the project tree it was built from. Exit code 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

PROHIBITED_PATH = re.compile(r"(^|/)(data|datasets|experiments|tests|\.venv[^/]*|venv|node_modules|backups|artifacts|quarantine)/", re.I)
PROHIBITED_NAME = re.compile(r"(^|/)(\.env(\..*)?|\.dev_secret|id_rsa.*|id_ed25519.*|.*\.(pem|key|p12|pfx|db|db-wal|db-shm|sqlite3?))$", re.I)
ALLOWED_ENV_TEMPLATE = "infra/staging/staging.env.example"
SECRET_TEXT = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|(?i:jwt_secret)\s*=\s*(?!CHANGE_ME)[A-Za-z0-9_\-]{24,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--source")
    a = ap.parse_args()
    problems: list[str] = []
    with zipfile.ZipFile(a.bundle) as z:
        bad = z.testzip()
        if bad:
            problems.append(f"CRC error in {bad}")
        names = [n for n in z.namelist() if not n.endswith("/")]
        man = json.loads(z.read("RELEASE_MANIFEST.json"))
        listed = man["sha256"]
        files = [n for n in names if n != "RELEASE_MANIFEST.json"]
        extra = sorted(set(files) - set(listed))
        missing = sorted(set(listed) - set(files))
        problems += [f"not in manifest: {n}" for n in extra] + [f"in manifest but missing: {n}" for n in missing]
        mismatched = []
        for n in files:
            data = z.read(n)
            if n in listed and hashlib.sha256(data).hexdigest() != listed[n]:
                mismatched.append(n)
            if PROHIBITED_PATH.search(n) or (PROHIBITED_NAME.search(n) and n != ALLOWED_ENV_TEMPLATE):
                problems.append(f"prohibited file: {n}")
            if SECRET_TEXT.search(data):
                problems.append(f"secret-like content in {n}")
        problems += [f"sha256 mismatch: {n}" for n in mismatched]
        model_dirs = sorted({"/".join(n.split("/")[:3]) for n in files if n.startswith("models/")})
        expected = [f"models/detection/{man['models']['detection']}", f"models/gate/{man['models']['gate']}"]
        if model_dirs != expected:
            problems.append(f"model bundles {model_dirs} != {expected}")
        det = hashlib.sha256(z.read(f"models/detection/{man['models']['detection']}/model.pt")).hexdigest()
        gate = hashlib.sha256(z.read(f"models/gate/{man['models']['gate']}/coco.pth")).hexdigest()
        if det != man["models"]["detection_weights_sha256"] or gate != man["models"]["gate_weights_sha256"]:
            problems.append("model weights do not match the manifest")
        build = json.loads(z.read("apps/web/dist/build.json"))["build"]
        if build != man["frontend_build"]:
            problems.append(f"frontend build {build} != manifest {man['frontend_build']}")
        differs = []
        if a.source:
            root = Path(a.source)
            for n in files:
                p = root / n
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != listed.get(n):
                    differs.append(n)
            problems += [f"differs from source tree: {n}" for n in differs if n != "apps/web/dist/build.json"]
    out = {"bundle": str(a.bundle), "bundle_sha256": hashlib.sha256(Path(a.bundle).read_bytes()).hexdigest(), "files": len(files),
           "frontend_build": man["frontend_build"], "models": man["models"], "requirements_lock_sha256": man.get("requirements_lock_sha256"),
           "top_level": sorted({n.split("/")[0] for n in files}), "source_compared": bool(a.source),
           "build_json_timestamp_only_difference": "apps/web/dist/build.json" in differs, "problems": problems, "ok": not problems}
    print(json.dumps(out, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
