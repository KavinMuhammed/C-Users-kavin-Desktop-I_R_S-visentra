"""Restore drill: restore a checkpoint into an EMPTY folder and prove the restored instance is usable.

    python scripts/restore_drill.py <checkpoint_dir> <new_empty_folder> [--out result.json]

Steps (never touches the live data/ folder or the checkpoint):
  1. copy primary-visentra.db to <dest>/data/visentra.db; extract objects.zip into <dest>/  (data/objects/...)
  2. PRAGMA integrity_check and foreign_key_check on the restored database
  3. every stored object the database refers to (inspection media, renditions, text-evidence sources, available reports,
     committed storage objects) is read through the application's own storage layer and its sha256 compared
  4. the application is started against the restored folder: migrate() must be a no-op at the code's head revision,
     /api/health/ready must report the database and object store "ok", and the row counts must equal the backup's.
  5. models.zip (when present) is restored and checked against hashes.json; the detector must load from the restored copy,
     analyse a restored original uploaded by a new account, and the stored original must read back byte-for-byte.
     These writes go to the restored copy only; the checkpoint database's sha256 is compared before and after.
Exit code 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBJECT_QUERIES = {
    "inspection_media": "select object_key, sha256 from inspection_media",
    "media_renditions": "select object_key, sha256 from media_renditions",
    "text_evidence": "select object_key, sha256 from text_evidence",
    "inspection_reports": "select object_key, sha256 from inspection_reports where status = 'available'",
    "storage_objects": "select object_key, sha256 from storage_objects where state = 'committed'",
}
COUNTED = ("users", "workspaces", "inspections", "inspection_media", "media_renditions", "analysis_runs", "defect_predictions",
           "human_findings", "finding_decisions", "inspection_reports", "text_evidence", "audit_events")


def counts(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in COUNTED if t in have}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("checkpoint")
    ap.add_argument("dest")
    ap.add_argument("--out")
    a = ap.parse_args()
    cp, dest = Path(a.checkpoint).resolve(), Path(a.dest).resolve()
    if dest.exists():
        print(f"{dest} exists; the drill only restores into a new folder", file=sys.stderr)
        return 2
    if ROOT / "data" in (dest, *dest.parents):
        print("refusing to restore inside the live data folder", file=sys.stderr)
        return 2
    res: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "checkpoint": str(cp), "dest": str(dest), "ok": True}

    # 1. restore
    (dest / "data").mkdir(parents=True)
    shutil.copy2(cp / "primary-visentra.db", dest / "data" / "visentra.db")
    if (cp / "objects.zip").is_file():
        with zipfile.ZipFile(cp / "objects.zip") as z:
            for n in z.namelist():                       # names are data/objects/<key>; refuse anything else
                if not n.startswith("data/objects/") or ".." in Path(n).parts:
                    raise SystemExit(f"unexpected archive entry {n!r}")
            z.extractall(dest)
        res["objects_restored"] = len(list((dest / "data" / "objects").rglob("*")))
    else:
        res["objects_restored"] = 0
        res["note"] = "checkpoint has no objects.zip (media was not copied); object checks will fail"
    # model bundles from the checkpoint itself (models.zip, 27 Oct 2026 on), verified against the checkpoint's hashes.json
    models_from_checkpoint = (cp / "models.zip").is_file()
    if models_from_checkpoint:
        with zipfile.ZipFile(cp / "models.zip") as z:
            for n in z.namelist():
                if not n.startswith("models/") or ".." in Path(n).parts:
                    raise SystemExit(f"unexpected archive entry {n!r}")
            z.extractall(dest)
        rec = {k: v for k, v in json.loads((cp / "hashes.json").read_text()).items() if k.startswith("models/")}
        bad = [k for k, (_n, s) in rec.items() if not (dest / k).is_file() or hashlib.sha256((dest / k).read_bytes()).hexdigest() != s]
        res["models"] = {"source": "checkpoint models.zip", "files": len(rec), "mismatched_or_missing": bad[:10]}
        res["ok"] &= not bad
    else:
        res["models"] = {"source": "live models/ folder (checkpoint has no models.zip)"}
    db_sha_before = hashlib.sha256((cp / "primary-visentra.db").read_bytes()).hexdigest()
    before = counts(dest / "data" / "visentra.db")

    # 2. database integrity
    con = sqlite3.connect(dest / "data" / "visentra.db")
    res["integrity_check"] = con.execute("pragma integrity_check").fetchone()[0]
    res["foreign_key_violations"] = len(con.execute("pragma foreign_key_check").fetchall())
    refs = {t: con.execute(q).fetchall() for t, q in OBJECT_QUERIES.items()}
    con.close()
    res["ok"] &= res["integrity_check"] == "ok" and res["foreign_key_violations"] == 0

    # 4 (set-up). the application, pointed at the restored folder only
    os.environ.update({"VISENTRA_DATA_DIR": str(dest / "data"), "DATABASE_URL": f"sqlite:///{(dest / 'data' / 'visentra.db').as_posix()}",
                       "VISENTRA_ENV": "test", "JWT_SECRET": "restore-drill-" + "x" * 40, "STORAGE_BACKEND": "local",
                       "LOG_LEVEL": "WARNING", "REGISTRATION_OPEN": "true",
                       # the restored model bundles, pinned to the operational ids
                       "VISIONTRA_INSPECTION_MODEL": "VT-DET-0002", "VISIONTRA_DOMAIN_GATE": "VT-GATE-0002",
                       **({"VISIONTRA_DETECTION_MODELS_DIR": str(dest / "models" / "detection"),
                           "VISIONTRA_DOMAIN_GATE_DIR": str(dest / "models" / "gate")} if models_from_checkpoint else {})})
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from visentra_api.config import settings
    assert Path(settings.data_dir).resolve() == (dest / "data").resolve(), settings.data_dir
    from visentra_api.storage import storage

    # 3. every referenced object, read through the app's storage layer
    objs = {}
    for t, rows in refs.items():
        missing, wrong = [], []
        for key, sha in rows:
            try:
                data = storage.get(key)
            except FileNotFoundError:
                missing.append(key); continue
            if sha and hashlib.sha256(data).hexdigest() != sha:
                wrong.append(key)
        objs[t] = {"referenced": len(rows), "missing": len(missing), "mismatched": len(wrong), "examples": (missing + wrong)[:5]}
        res["ok"] &= not missing and not wrong
    res["objects"] = objs

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from fastapi.testclient import TestClient
    from visentra_api.main import app, migrate
    cfg = Config(str(ROOT / "apps" / "api" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    migrate()
    con = sqlite3.connect(f"file:{(dest / 'data' / 'visentra.db').as_posix()}?mode=ro", uri=True)
    res["alembic"] = {"code_head": head, "restored": con.execute("select version_num from alembic_version").fetchone()[0]}
    con.close()
    res["ok"] &= res["alembic"]["code_head"] == res["alembic"]["restored"]
    with TestClient(app) as c:
        r = c.get("/api/health/ready")
        ready = r.json()
        after = counts(dest / "data" / "visentra.db")           # start-up and readiness must not change any row
        res["ready"] = {"http": r.status_code, **{k: v["status"] for k, v in ready["checks"].items()}}
        res["ok"] &= res["ready"]["database"] == "ok" and res["ready"]["object_storage"] == "ok"
        # 5. the restored instance works: a new account uploads a restored original; the restored detector analyses it
        #    and the stored copy reads back byte-for-byte (writes go to the restored copy only)
        import torch
        from visentra_api.inspection import analysis as AN
        eng, _ = AN.engine()
        res["detector"] = {"model_id": eng.model_id if eng else None, "bundle": str(eng.bundle) if eng else None}
        if models_from_checkpoint:
            res["ok"] &= bool(eng) and str(dest) in str(eng.bundle)
        con = sqlite3.connect(f"file:{(dest / 'data' / 'visentra.db').as_posix()}?mode=ro", uri=True)
        key, mime = con.execute("select object_key, mime from inspection_media where mime = 'image/jpeg' order by uploaded_at limit 1").fetchone()
        con.close()
        sample = storage.get(key)
        reg = c.post("/api/auth/register", json={"name": "Restore Drill", "email": "restore-drill@example.org", "password": "correct horse 42"})
        h = {"X-CSRF-Token": reg.json()["csrf_token"]}
        up = c.post("/api/inspections", headers=h, files={"file": ("restored.jpg", sample, mime)}, data={"component": "fuselage"})
        body = up.json()
        back = c.get(body["media"][0]["urls"]["original"]) if up.status_code == 201 else None
        res["upload_on_restored"] = {"http": up.status_code, "analysis_mode": body.get("analysis_mode"), "status": body.get("status"),
                                     "predictions": len(body.get("predictions", [])), "original_roundtrip": bool(back is not None and back.content == sample),
                                     "torch": torch.__version__}
        res["ok"] &= up.status_code == 201 and body.get("analysis_mode") == "model" and res["upload_on_restored"]["original_roundtrip"]
    res["row_counts"] = before
    res["row_counts_unchanged_by_start"] = before == after
    res["ok"] &= before == after
    res["checkpoint_db_unchanged"] = hashlib.sha256((cp / "primary-visentra.db").read_bytes()).hexdigest() == db_sha_before
    res["ok"] &= res["checkpoint_db_unchanged"]
    res["finished_at"] = datetime.now(timezone.utc).isoformat()
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
