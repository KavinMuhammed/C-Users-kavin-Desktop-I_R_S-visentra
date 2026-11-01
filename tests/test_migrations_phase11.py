"""Phase 11 migration safety (revision c4e1a9d2b7f3, additive): upgrade of an existing Phase 10 database with inspections
preserves every Phase 10 row and schema object; the review/report tables are append-only in the database; downgrade
refuses while they hold data and, when explicitly allowed, returns to the Phase 10 schema with Phase 10 data intact.

Throw-away SQLite files in temp directories, Alembic in a subprocess (same harness as test_migrations_phase10.py).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from test_migrations_phase10 import HEAD, PHASE10, PRE, run, revision, seed_legacy, tables

PHASE11 = "c4e1a9d2b7f3"
NEW = {"human_findings", "finding_decisions", "inspection_reports", "analysis_runs", "analysis_media", "defect_predictions"}
P10_TABLES = ("users", "workspaces", "workspace_members", "audit_events", "inspections", "inspection_media", "media_renditions", "storage_objects",
              "inspection_ref_counters")


def seed_phase10(db: Path) -> None:
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO inspections (id, workspace_id, ref, component, status, created_by, revision, notes) "
                  "VALUES ('i1', 'w1', 'INS-2026-000001', 'fuselage', 'awaiting_review', 'u1', 3, 'raking light')")
        c.execute("INSERT INTO inspection_ref_counters (workspace_id, year, last_value) VALUES ('w1', 2026, 1)")
        c.execute("INSERT INTO storage_objects (object_key, store, purpose, state, sha256, size_bytes, owner_type, owner_id, committed_at) "
                  "VALUES ('inspection-originals/x.jpg', 'objects', 'media_original', 'committed', ?, 10, 'inspection_media', 'm1', '2026-10-12 10:00:00')", ("a" * 64,))
        c.execute("INSERT INTO inspection_media (id, inspection_id, workspace_id, ordinal, kind, object_key, filename, mime, size_bytes, sha256, width, height, "
                  "uploaded_by) VALUES ('m1', 'i1', 'w1', 1, 'image', 'inspection-originals/x.jpg', 'panel.jpg', 'image/jpeg', 10, ?, 800, 600, 'u1')", ("a" * 64,))


def schema(db: Path, names) -> dict:
    with sqlite3.connect(db) as c:
        return {t: c.execute("SELECT sql FROM sqlite_master WHERE tbl_name = ? ORDER BY name", (t,)).fetchall() for t in names}


def snapshot(db: Path) -> dict:
    with sqlite3.connect(db) as c:
        return {t: c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in P10_TABLES}


@pytest.fixture()
def phase10_db():
    tmp = Path(tempfile.mkdtemp(prefix="vt-mig11-"))
    assert run(tmp, "upgrade", PRE).returncode == 0
    seed_legacy(tmp / "v.db")
    assert run(tmp, "upgrade", PHASE10).returncode == 0
    try:
        seed_phase10(tmp / "v.db")
    except sqlite3.Error as ex:                                         # column drift in this helper must fail loudly, not skip
        raise AssertionError(f"seed does not match the Phase 10 schema: {ex}") from ex
    return tmp


def test_upgrade_of_a_phase10_database_is_additive_and_preserves_everything(phase10_db):
    db = phase10_db / "v.db"
    before, ddl = snapshot(db), schema(db, P10_TABLES)
    p = run(phase10_db, "migrate")                                     # the start-up path: backup, then upgrade
    assert p.returncode == 0, p.stderr
    assert revision(db) == HEAD and NEW <= tables(db)
    assert snapshot(db) == before, "Phase 10 rows changed"
    assert schema(db, P10_TABLES) == ddl, "a Phase 10 table, index or trigger changed"
    with sqlite3.connect(db) as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == [] and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backups = list((phase10_db / "backups").glob(f"pre-migration-{PHASE10}-to-{HEAD}-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as b:
        assert b.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PHASE10
        assert b.execute("SELECT ref FROM inspections").fetchall() == [("INS-2026-000001",)]


def test_review_rows_are_append_only_in_the_migrated_database(phase10_db):
    db = phase10_db / "v.db"
    assert run(phase10_db, "upgrade", PHASE11).returncode == 0
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO human_findings (id, inspection_id, media_id, category, taxonomy_version, box_x, box_y, box_w, box_h, created_by) "
                  "VALUES ('h1', 'i1', 'm1', 'dent', 'TAX-1.0', 0.1, 0.1, 0.2, 0.2, 'u1')")
        c.execute("INSERT INTO finding_decisions (id, inspection_id, target_type, human_finding_id, kind, decision, actor_id, inspection_revision) "
                  "VALUES ('d1', 'i1', 'human_finding', 'h1', 'decision', 'rejected', 'u1', 4)")
        c.commit()
        for stmt in ("UPDATE finding_decisions SET decision = 'confirmed'", "DELETE FROM finding_decisions"):
            with pytest.raises(sqlite3.DatabaseError, match="VT_IMMUTABLE"):
                c.execute(stmt)
        assert c.execute("SELECT decision FROM finding_decisions").fetchall() == [("rejected",)]


def test_downgrade_refuses_over_review_data_and_keeps_phase10_data_when_allowed(phase10_db):
    db = phase10_db / "v.db"
    assert run(phase10_db, "upgrade", PHASE11).returncode == 0
    before = snapshot(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO human_findings (id, inspection_id, media_id, category, taxonomy_version, box_x, box_y, box_w, box_h, created_by) "
                  "VALUES ('h1', 'i1', 'm1', 'dent', 'TAX-1.0', 0.1, 0.1, 0.2, 0.2, 'u1')")
    p = run(phase10_db, "downgrade", PHASE10)
    assert p.returncode != 0 and "allow_data_loss" in (p.stderr + p.stdout)
    assert revision(db) == PHASE11 and "human_findings" in tables(db)
    p = run(phase10_db, "downgrade", PHASE10, "allow_data_loss=1")
    assert p.returncode == 0, p.stderr
    assert revision(db) == PHASE10 and not (NEW & tables(db))
    assert snapshot(db) == before, "Phase 10 rows changed by the downgrade"
    assert run(phase10_db, "upgrade", PHASE11).returncode == 0 and revision(db) == PHASE11          # and forward again


def test_empty_phase11_round_trip():
    tmp = Path(tempfile.mkdtemp(prefix="vt-mig11-"))
    assert run(tmp, "upgrade", PHASE11).returncode == 0
    assert run(tmp, "downgrade", PHASE10).returncode == 0                                       # no review rows: allowed
    assert run(tmp, "upgrade", PHASE11).returncode == 0 and revision(tmp / "v.db") == PHASE11
