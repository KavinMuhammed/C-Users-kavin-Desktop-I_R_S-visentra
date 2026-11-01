"""Phase 10 migration safety: upgrade of an existing (pre-Phase-10) database, automatic backup, data preservation,
refusal to downgrade over data, explicit downgrade, re-upgrade.

Each test builds its own throw-away SQLite file in a temp directory and runs Alembic in a subprocess pointed at it,
so the suite's own database and any real database are never touched.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API = REPO / "apps" / "api"
PRE, PHASE10 = "d840f8869c0c", "5b1e0c7a2d94"
# Phase 11: the start-up path (`migrate`) ends at the current head; the explicit up/down tests stay pinned to Phase 10.
def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


HEAD = _head()

RUNNER = r"""
import json, sys
from alembic import command
from alembic.config import Config
from visentra_api.config import settings
cfg = Config(sys.argv[1]); cfg.set_main_option("script_location", sys.argv[2]); cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
op, arg = sys.argv[3], sys.argv[4]
if op == "upgrade": command.upgrade(cfg, arg)
elif op == "downgrade":
    if len(sys.argv) > 5: cfg.cmd_opts = type("o", (), {"x": [sys.argv[5]]})()
    command.downgrade(cfg, arg)
elif op == "migrate":
    from visentra_api.main import migrate; migrate()
print("OK")
"""


def run(tmp: Path, op: str, arg: str = "head", extra: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp / 'v.db'}", "VISENTRA_DATA_DIR": str(tmp), "VISENTRA_ENV": "test",
           "JWT_SECRET": "migration-test-" + "x" * 40, "LOG_LEVEL": "WARNING", "PYTHONPATH": f"{API}{os.pathsep}{REPO / 'ml'}"}
    args = [sys.executable, "-c", RUNNER, str(API / "alembic.ini"), str(API / "alembic"), op, arg] + ([extra] if extra else [])
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=str(tmp), timeout=180)


def seed_legacy(db: Path) -> dict:
    """Rows a pre-Phase-10 installation really has: a user, a workspace, membership, audit history, an idempotency record."""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO users (id, email, name, password_hash, is_active, created_at) VALUES ('u1', 'legacy@example.org', 'Legacy', 'x', 1, '2026-10-01 10:00:00')")
    c.execute("INSERT INTO workspaces (id, name, owner_id, personal, created_at) VALUES ('w1', 'Legacy WS', 'u1', 1, '2026-10-01 10:00:00')")
    c.execute("INSERT INTO workspace_members (workspace_id, user_id, role, created_at) VALUES ('w1', 'u1', 'OWNER', '2026-10-01 10:00:00')")
    for i in range(25):
        c.execute("INSERT INTO audit_events (id, at, action, resource_type, result, meta) VALUES (?, '2026-10-02 10:00:00', 'legacy.event', 'x', 'success', '{}')", (f"a{i}",))
    c.execute("INSERT INTO idempotency_keys (id, scope, key, status_code, response, created_at) VALUES ('k1', 'user:u1:x', 'key-1', 201, '{\"id\": 1}', '2026-10-02 10:00:00')")
    c.commit()
    snap = {t: c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("users", "workspaces", "workspace_members", "audit_events")}
    c.close()
    return snap


def tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def revision(db: Path) -> str:
    with sqlite3.connect(db) as c:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_upgrade_existing_database_backs_up_and_preserves_data():
    tmp = Path(tempfile.mkdtemp(prefix="vt-mig-"))
    db = tmp / "v.db"
    assert run(tmp, "upgrade", PRE).returncode == 0
    snap = seed_legacy(db)
    p = run(tmp, "migrate")                                            # the start-up path: backup, then upgrade
    assert p.returncode == 0, p.stderr
    assert revision(db) == HEAD
    assert {"inspections", "inspection_media", "media_renditions", "storage_objects", "inspection_ref_counters",
            "experimental_runs", "experimental_predictions"} <= tables(db)
    with sqlite3.connect(db) as c:                                    # every legacy row is still there, unchanged
        for t, rows in snap.items():
            assert c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() == rows, t
        assert c.execute("SELECT scope, key, status_code, request_hash FROM idempotency_keys").fetchall() == [("user:u1:x", "key-1", 201, None)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == [] and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = sorted((tmp / "backups").glob(f"pre-migration-{PRE}-to-{HEAD}-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as b:                             # the backup is the pre-migration database
        assert b.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PRE
        assert b.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 25 and b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "inspections" not in {r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    p = run(tmp, "migrate")                                            # already at head: no second backup, no change
    assert p.returncode == 0 and len(list((tmp / "backups").glob("*.db"))) == 1


def test_downgrade_refuses_to_destroy_data_unless_explicitly_allowed():
    tmp = Path(tempfile.mkdtemp(prefix="vt-mig-"))
    db = tmp / "v.db"
    assert run(tmp, "upgrade", PRE).returncode == 0
    snap = seed_legacy(db)
    assert run(tmp, "upgrade", PHASE10).returncode == 0
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('i1', 'w1', 'INS-2026-000001', 'fuselage', 'awaiting_review', 'u1')")
    p = run(tmp, "downgrade", PRE)
    assert p.returncode != 0 and "refusing to downgrade" in p.stderr
    assert revision(db) == PHASE10 and "inspections" in tables(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT ref FROM inspections").fetchall() == [("INS-2026-000001",)]
    p = run(tmp, "downgrade", PRE, "allow_data_loss=1")
    assert p.returncode == 0, p.stderr
    assert revision(db) == PRE and "inspections" not in tables(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT * FROM users ORDER BY 1").fetchall() == snap["users"]
        assert "request_hash" not in {r[1] for r in c.execute("PRAGMA table_info(idempotency_keys)")}
    assert run(tmp, "upgrade", PHASE10).returncode == 0 and revision(db) == PHASE10          # and forward again


def test_empty_database_round_trip():
    tmp = Path(tempfile.mkdtemp(prefix="vt-mig-"))
    assert run(tmp, "upgrade", PHASE10).returncode == 0
    assert run(tmp, "downgrade", PRE).returncode == 0                                  # no rows: allowed without the flag
    assert run(tmp, "upgrade", PHASE10).returncode == 0
    assert revision(tmp / "v.db") == PHASE10
