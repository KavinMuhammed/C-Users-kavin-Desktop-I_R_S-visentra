"""Reconcile the object store with the database (Phase 10; docs/PHASE_10_RECOVERY.md).

    python scripts/reconcile_storage.py                 # resolve stale write intents, remove old temporary files
    python scripts/reconcile_storage.py --verify        # also check that every committed object still exists
    python scripts/reconcile_storage.py --dry-run       # report only; change nothing
    python scripts/reconcile_storage.py --grace 900     # only intents older than this many seconds (default STORAGE_INTENT_GRACE_S)

Uses the same configuration as the API (DATABASE_URL, VISENTRA_DATA_DIR, STORAGE_BACKEND). It never lists the bucket
and never touches an object without an intent row. Output is a JSON summary of counts (no keys or paths).
Exit code 0 = nothing wrong, 1 = missing or mismatched objects or errors were found (see the report).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "ml")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grace", type=int, default=None)
    a = ap.parse_args()
    from visentra_api.db import SessionLocal
    from visentra_api.inspection.objects import reconcile
    from visentra_api.storage import storage
    with SessionLocal() as db:
        rep = reconcile(db, storage, grace_s=a.grace, verify=a.verify, dry_run=a.dry_run)
    print(json.dumps(rep.as_dict(), indent=2))
    return 1 if (rep.missing or rep.size_mismatch or rep.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
