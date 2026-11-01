"""Recovery checkpoint: source zip (code, docs, tests, dataset manifests), online SQLite backup, sha256 manifest.

    python scripts/make_checkpoint.py <project_root> <new_checkpoint_dir> [<data_dir>]

<data_dir> is the server's VISENTRA_DATA_DIR (default <project_root>/data). Archive names stay data/objects/... either way.

Read-only for the project. The DB is copied with the SQLite online-backup API, so the server may keep running.
The destination must not exist. Verify afterwards with scripts/verify_checkpoint.py.
"""
import hashlib, json, os, shutil, sqlite3, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]); DEST = Path(sys.argv[2])
DATA = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "data"
if not (DATA / "visentra.db").is_file():
    raise SystemExit(f"no database at {DATA / 'visentra.db'}: pass the server's VISENTRA_DATA_DIR as the third argument")
DEST.mkdir(parents=True, exist_ok=False)
SRC_DIRS = ["apps", "ml", "scripts", "tests", "docs", "infra"]
SRC_FILES = ["package.json", "pyproject.toml", "README.md", "requirements.txt", "requirements-dev.txt", "requirements-ml.txt",
             ".gitignore", ".env.example", "start-visiontra.bat", "train-detector-gpu.bat", ".dockerignore", "requirements.lock"]
SKIP = ("__pycache__", ".pytest_cache", "node_modules")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

n = 0
with zipfile.ZipFile(DEST / "source.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for d in SRC_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and not any(s in p.parts for s in SKIP):
                z.write(p, p.relative_to(ROOT)); n += 1
    for f in SRC_FILES:
        if (ROOT / f).is_file():
            z.write(ROOT / f, f); n += 1
    for p in (ROOT / "datasets").rglob("*"):          # manifests/splits/annotations only, not images
        if p.is_file() and p.suffix.lower() in (".csv", ".json", ".jsonl", ".md", ".txt", ".yaml") and p.stat().st_size < 50_000_000:
            z.write(p, p.relative_to(ROOT)); n += 1
for name in ["visentra.db"]:
    s = sqlite3.connect(f"file:{(DATA / name).as_posix()}?mode=ro", uri=True); d = sqlite3.connect(DEST / f"primary-{name}")
    s.backup(d); ok = d.execute("pragma integrity_check").fetchone()[0]; d.close(); s.close()
# Inspection media (originals, renditions, text-evidence sources). Copied AFTER the database: the app writes an object
# before committing the row that references it, so every object the backed-up database refers to already exists here.
# Images are already compressed, so the archive stores them as-is.
n_obj = 0
with zipfile.ZipFile(DEST / "objects.zip", "w", zipfile.ZIP_STORED) as z:
    for p in sorted((DATA / "objects").rglob("*")):
        if p.is_file():
            z.write(p, "data/objects/" + p.relative_to(DATA / "objects").as_posix()); n_obj += 1
# Model bundles (detector + aircraft-surface gate, ~160 MB): weights are not in Git, so a restore needs them from here.
n_mod = 0
with zipfile.ZipFile(DEST / "models.zip", "w", zipfile.ZIP_STORED) as z:
    for p in sorted((ROOT / "models").rglob("*")):
        if p.is_file():
            z.write(p, p.relative_to(ROOT).as_posix()); n_mod += 1
hashes = {}
for d in ["models", "experiments"]:
    for p in sorted((ROOT / d).rglob("*")):
        if p.is_file():
            hashes[p.relative_to(ROOT).as_posix()] = [p.stat().st_size, sha(p)]
for p in sorted((DATA / "objects").rglob("*")):
    if p.is_file():
        hashes["data/objects/" + p.relative_to(DATA / "objects").as_posix()] = [p.stat().st_size, sha(p)]
(DEST / "hashes.json").write_text(json.dumps(hashes, indent=0))
with zipfile.ZipFile(DEST / "source.zip") as z:
    bad = z.testzip()
manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "root": str(ROOT), "data_dir": str(DATA), "source_files": n, "zip_crc_first_bad": bad,
            "db_backup_integrity": ok, "hashed_files": len(hashes), "source_zip_sha256": sha(DEST / "source.zip"),
            "db_backup_sha256": sha(DEST / "primary-visentra.db"), "objects_files": n_obj, "objects_zip_sha256": sha(DEST / "objects.zip"),
            "models_files": n_mod, "models_zip_sha256": sha(DEST / "models.zip"),
            # what a restore can and cannot take from this folder
            "archived": ["source.zip (apps, ml, scripts, tests, docs, infra, dataset manifests)", "primary-visentra.db", "objects.zip (data/objects)",
                         "models.zip (models/)"],
            "hashed_only": {"experiments/": "training runs and history; sizes and sha256 in hashes.json, files NOT copied (restore from the "
                                            "live folder or an earlier full backup)"},
            "not_included": ["configuration and secrets (.env / environment variables)", "datasets images", "virtual environments (rebuild "
                             "from requirements.lock)"]}
(DEST / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
print(json.dumps(manifest, indent=1))
