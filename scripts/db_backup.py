"""Back up the VisionTra database and object store (Phase 10; docs/PHASE_10_RECOVERY.md).

    python scripts/db_backup.py                     # SQLite: consistent online copy to <data_dir>/backups/, verified
    python scripts/db_backup.py --objects           # also copy <data_dir>/objects (inspection images) next to it

Nothing is deleted or overwritten: every backup gets a new time-stamped name. For PostgreSQL the script prints the
pg_dump command to run instead (it never prints the password). Output: a JSON summary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "ml")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--objects", action="store_true", help="also copy the local object store")
    a = ap.parse_args()
    from visentra_api.config import settings
    from visentra_api.dbbackup import backup_sqlite, current_revision, sqlite_path
    out: dict = {}
    path = sqlite_path(settings.database_url)
    if path is None:
        out["database"] = {"engine": "postgresql", "action": "run: pg_dump --format=custom --file=visentra-$(date -u +%Y%m%dT%H%M%SZ).dump \"$DATABASE_URL\""}
    elif not path.exists():
        print(json.dumps({"error": "database file not found"}))
        return 1
    else:
        dest = backup_sqlite(path, settings.data_dir / "backups", f"manual-{current_revision(path) or 'unversioned'}")
        out["database"] = {"engine": "sqlite", "backup": dest.name, "bytes": dest.stat().st_size, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                           "integrity_check": "ok"}
    if a.objects:
        if settings.storage_backend != "local":
            out["objects"] = {"action": "use the bucket's versioning or replication (S3/MinIO); not copied by this script"}
        else:
            src = settings.data_dir / "objects"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dst = settings.data_dir / "backups" / f"objects-{stamp}"
            if src.exists():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.tmp"))
            n = sum(1 for p in dst.rglob("*") if p.is_file()) if dst.exists() else 0
            out["objects"] = {"backup": dst.name, "files": n}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
