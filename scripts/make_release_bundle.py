"""Build the release bundle that is copied to a staging/production host (docs/STAGING_PLAN.md).

    python scripts/make_release_bundle.py <output.zip>

Allow-list only: application code, the built frontend, the operational model bundles, dependency files, operator scripts,
staging configuration templates and the runbooks. Never included: .env or any secret, data/ (databases, images),
datasets, experiments, tests, virtual environments. RELEASE_MANIFEST.json inside the zip records every file's sha256,
the frontend build id, the model ids and weight checksums, and the requirements.lock checksum; the smoke test compares
the deployed server against it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {"detection": "VT-DET-0002", "gate": "VT-GATE-0002"}
INCLUDE_DIRS = ["apps/api/visentra_api", "apps/api/alembic", "apps/web/dist", "ml/visentra_ml",
                f"models/detection/{MODELS['detection']}", f"models/gate/{MODELS['gate']}", "infra/staging"]
INCLUDE_FILES = ["apps/api/alembic.ini", "pyproject.toml", "requirements.txt", "requirements.lock", "requirements-dev.txt", "README.md",
                 "scripts/create_user.py", "scripts/make_checkpoint.py", "scripts/verify_checkpoint.py", "scripts/restore_drill.py",
                 "scripts/compare_db_rows.py", "scripts/staging_smoke.py", "scripts/verify_release_bundle.py", "docs/DEPLOYMENT_RUNBOOK.md", "docs/STAGING_PLAN.md"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "data"}
FORBIDDEN = (".env", ".dev_secret", ".db", ".db-wal", ".db-shm", ".sqlite")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__); return 2
    out = Path(sys.argv[1]).resolve()
    if out.exists():
        print(f"{out} exists", file=sys.stderr); return 2
    build = json.loads((ROOT / "apps/web/dist/build.json").read_text(encoding="utf-8"))["build"]
    files: list[Path] = []
    for d in INCLUDE_DIRS:
        files += [p for p in sorted((ROOT / d).rglob("*")) if p.is_file() and not (SKIP_PARTS & set(p.relative_to(ROOT).parts))]
    files += [ROOT / f for f in INCLUDE_FILES]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("missing: " + ", ".join(str(m.relative_to(ROOT)) for m in missing), file=sys.stderr); return 1
    bad = [f for f in files if f.name.lower().endswith(FORBIDDEN) or f.name.lower() == ".env"]
    if bad:
        print("refusing to bundle secrets or data: " + ", ".join(str(b.relative_to(ROOT)) for b in bad), file=sys.stderr); return 1
    entries = {f.relative_to(ROOT).as_posix(): sha(f) for f in files}
    det = json.loads((ROOT / f"models/detection/{MODELS['detection']}/metadata.json").read_text(encoding="utf-8"))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(), "frontend_build": build,
        "models": {"detection": MODELS["detection"], "detection_weights_sha256": det["files"]["model.pt"]["sha256"],
                   "gate": MODELS["gate"], "gate_weights_sha256": entries[f"models/gate/{MODELS['gate']}/coco.pth"]},
        "requirements_lock_sha256": entries["requirements.lock"], "files": len(entries), "sha256": entries,
        "install": "see docs/STAGING_PLAN.md: venv, pip install -r requirements.lock (Windows/CPython 3.11) or requirements.txt "
                   "(Linux, CPU torch index), pip install --no-deps -e ., copy infra/staging/staging.env.example to .env",
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
        z.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, indent=1))
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
    print(json.dumps({"bundle": str(out), "bytes": out.stat().st_size, "sha256": sha(out), "files": len(entries),
                      "frontend_build": build, "models": manifest["models"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
