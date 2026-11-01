"""Verify a recovery checkpoint folder: archive CRCs, DB backup integrity, recorded sha256 values, live-vs-backup counts.

    python scripts/verify_checkpoint.py <checkpoint_dir> [--out artifacts/recovery/checkpoint_verification.json]

Read-only for the checkpoint and the live database (SQLite opened with mode=ro).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def db_summary(p: Path) -> dict:
    d = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in d.execute("select name from sqlite_master where type='table'")]
        out = {"integrity": d.execute("pragma integrity_check").fetchone()[0], "tables": len(tables)}
        if "inspections" in tables:
            out["inspections"] = d.execute("select count(*) from inspections").fetchone()[0]
            out["inspections_soft_deleted"] = d.execute("select count(*) from inspections where deleted_at is not null").fetchone()[0]
        if "workspaces" in tables:
            out["workspaces"] = d.execute("select count(*) from workspaces").fetchone()[0]
        if "alembic_version" in tables:
            out["alembic"] = [r[0] for r in d.execute("select version_num from alembic_version")]
        return out
    finally:
        d.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint")
    ap.add_argument("--out")
    ap.add_argument("--data-dir", help="the server's VISENTRA_DATA_DIR for the live summary (default <project>/data)")
    a = ap.parse_args()
    cp = Path(a.checkpoint)
    res = {"checked_at": datetime.now(timezone.utc).isoformat(), "checkpoint": str(cp), "archives": {}, "ok": True}
    man = json.loads((cp / "MANIFEST.json").read_text()) if (cp / "MANIFEST.json").is_file() else {}
    res["manifest"] = man
    for z in sorted(cp.glob("*.zip")):
        with zipfile.ZipFile(z) as f:
            bad = f.testzip()
            res["archives"][z.name] = {"entries": len(f.namelist()), "first_bad": bad, "sha256": sha(z)}
            res["ok"] &= bad is None
    if man.get("source_zip_sha256"):
        res["source_zip_sha256_matches_manifest"] = res["archives"].get("source.zip", {}).get("sha256") == man["source_zip_sha256"]
        res["ok"] &= res["source_zip_sha256_matches_manifest"]
    for db in sorted(cp.glob("*.db")):
        res.setdefault("db_backups", {})[db.name] = {**db_summary(db), "sha256": sha(db)}
        res["ok"] &= res["db_backups"][db.name]["integrity"] == "ok"
    if man.get("db_backup_sha256") and "primary-visentra.db" in res.get("db_backups", {}):
        res["db_backup_sha256_matches_manifest"] = res["db_backups"]["primary-visentra.db"]["sha256"] == man["db_backup_sha256"]
        res["ok"] &= res["db_backup_sha256_matches_manifest"]
    live_db = Path(a.data_dir) / "visentra.db" if a.data_dir else ROOT / "data" / "visentra.db"
    res["live"] = {str(live_db): db_summary(live_db)} if live_db.is_file() else {"note": f"no live database at {live_db}"}
    alt = ROOT / "apps/api/data/visentra.db"
    if alt.is_file():
        res["live"]["apps/api/data/visentra.db (alternate)"] = db_summary(alt)
    if (cp / "hashes.json").is_file():
        h = json.loads((cp / "hashes.json").read_text())
        bad = [k for k, (_n, s) in h.items() if k.startswith("models/") and (not (ROOT / k).is_file() or sha(ROOT / k) != s)]
        res["model_files_checked"] = sum(1 for k in h if k.startswith("models/")); res["model_files_mismatched"] = bad
        res["ok"] &= not bad
        # media: every recorded data/objects file must be inside objects.zip with the recorded size and sha256
        # (checkpoints made before 27 Oct 2026 hashed media but did not copy it; that is reported, not hidden)
        recorded = {k: v for k, v in h.items() if k.startswith("data/objects/")}
        if (cp / "objects.zip").is_file():
            with zipfile.ZipFile(cp / "objects.zip") as z:
                names = set(z.namelist())
                missing = [k for k in recorded if k not in names]
                wrong = [k for k in recorded if k in names and hashlib.sha256(z.read(k)).hexdigest() != recorded[k][1]]
            res["media"] = {"recorded": len(recorded), "archived": len(names), "missing": missing[:20], "mismatched": wrong[:20]}
            if man.get("objects_zip_sha256"):
                res["media"]["objects_zip_sha256_matches_manifest"] = sha(cp / "objects.zip") == man["objects_zip_sha256"]
                res["ok"] &= res["media"]["objects_zip_sha256_matches_manifest"]
            res["ok"] &= not missing and not wrong
        else:
            res["media"] = {"recorded": len(recorded), "archived": 0, "note": "no objects.zip: media was hashed but not copied (pre-27-Oct checkpoint)"}
        # model bundles inside the checkpoint (checkpoints from 27 Oct 2026 on); the check above compares the live folder
        rec_m = {k: v for k, v in h.items() if k.startswith("models/")}
        if (cp / "models.zip").is_file():
            with zipfile.ZipFile(cp / "models.zip") as z:
                names = set(z.namelist())
                missing = [k for k in rec_m if k not in names]
                wrong = [k for k in rec_m if k in names and hashlib.sha256(z.read(k)).hexdigest() != rec_m[k][1]]
            res["models_archive"] = {"recorded": len(rec_m), "archived": len(names), "missing": missing[:20], "mismatched": wrong[:20],
                                     "models_zip_sha256_matches_manifest": sha(cp / "models.zip") == man.get("models_zip_sha256")}
            res["ok"] &= not missing and not wrong and res["models_archive"]["models_zip_sha256_matches_manifest"]
        else:
            res["models_archive"] = {"recorded": len(rec_m), "archived": 0, "note": "no models.zip: model bundles must come from the live models/ folder"}
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
