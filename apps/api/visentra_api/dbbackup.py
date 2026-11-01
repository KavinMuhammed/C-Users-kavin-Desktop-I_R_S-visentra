"""Database backup before schema migration (Phase 10).

SQLite: an online, consistent copy made with SQLite's backup API (safe while the app runs; WAL included),
verified with `PRAGMA integrity_check`, written to `<data_dir>/backups/`. Nothing is ever deleted here.
PostgreSQL: the application cannot copy a server database; `migrate()` logs that `pg_dump` must be run
first (docs/PHASE_10_RECOVERY.md). Credentials are never logged.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url

from .config import settings

log = logging.getLogger("visentra.backup")


def sqlite_path(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database in (":memory:", "file::memory:") or parsed.query.get("mode") == "memory":
        return None
    return Path(parsed.database.removeprefix("file:"))


def current_revision(path: Path) -> str | None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


def backup_sqlite(src: Path, dest_dir: Path, label: str) -> Path:
    """Copy `src` to `dest_dir/<label>-<UTC time>.db` and verify the copy. Returns the backup path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = dest_dir / f"{label}-{stamp}.db"
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        d.close()
        s.close()
    if ok != "ok":
        raise RuntimeError(f"backup verification failed: {ok}")
    return dest


def backup_before_upgrade(head: str) -> Path | None:
    """Back up the SQLite database when it exists and is not already at `head`. Returns the backup path or None."""
    path = sqlite_path(settings.database_url)
    if path is None:
        if settings.database_url.startswith("postgresql"):
            log.warning("schema upgrade on PostgreSQL: take a pg_dump backup before upgrading (docs/PHASE_10_RECOVERY.md)")
        return None
    if not path.exists() or path.stat().st_size == 0:
        return None
    cur = current_revision(path)
    if cur is None or cur == head:
        return None
    dest = backup_sqlite(path, settings.data_dir / "backups", f"pre-migration-{cur}-to-{head}")
    log.warning("database backed up before migration %s -> %s: %s", cur, head, dest.name)
    return dest
