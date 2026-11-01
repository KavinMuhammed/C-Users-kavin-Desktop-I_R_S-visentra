"""Phase 5 architecture validation: catalog/DDL/code/Phase 2 consistency and the reference schema's constraints.

The SQL check builds the existing schema with Alembic in a temporary SQLite database, applies the portable
reference DDL and runs the constraint suite. Set ARCH_PG_URL to an EMPTY PostgreSQL database to also run the
PostgreSQL variant (alterations of existing tables and immutability triggers).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "architecture_check.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "VISENTRA_DATA_DIR")}
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True, timeout=600, env=env, cwd=str(ROOT))


def test_architecture_documents_are_consistent():
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "static checks: 0 problem(s)" in r.stdout


def test_reference_schema_constraints_on_sqlite():
    r = _run("--sql", "sqlite")
    m = re.search(r"sqlite: (\d+)/(\d+) constraint cases behaved as designed", r.stdout)
    assert m and m.group(1) == m.group(2) and int(m.group(2)) >= 50, r.stdout[-4000:] + r.stderr[-2000:]
    assert "  FAIL  " not in r.stdout


@pytest.mark.skipif(not os.environ.get("ARCH_PG_URL"), reason="set ARCH_PG_URL to an empty PostgreSQL database")
def test_reference_schema_constraints_on_postgresql():
    r = _run("--sql", os.environ["ARCH_PG_URL"])
    m = re.search(r"postgresql: (\d+)/(\d+) constraint cases behaved as designed", r.stdout)
    assert m and m.group(1) == m.group(2), r.stdout[-4000:] + r.stderr[-2000:]
