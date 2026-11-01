"""Validate dataset manifests against the files on disk and the Phase 3 rules.

    python scripts/datasets/validate_manifest.py          # structure, statuses, paths, sizes, counts
    python scripts/datasets/validate_manifest.py --full   # additionally re-hash every file (SHA-256)

Exit code 0 = all checks passed, 1 = problems found. Standard library only.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VISIONTRA_ROOT") or Path(__file__).resolve().parents[2])  # override for tests
DS = ROOT / "datasets"
STATUSES = ["DISCOVERED", "SOURCE_VERIFIED", "LICENCE_REVIEWED", "DOWNLOAD_PENDING", "DOWNLOADING", "DOWNLOADED", "INTEGRITY_VERIFIED", "PARTIAL", "FAILED", "RESTRICTED", "EXCLUDED"]
ACQUIRED = {"DOWNLOADED", "INTEGRITY_VERIFIED", "PARTIAL"}
REQUIRED = ["id", "name", "landing_url", "licence", "status", "decision"]
SECRET = re.compile(r"(api[_-]?key|token|password|secret|authorization|cookie)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}", re.I)


def walk_files(base: Path) -> list[str]:
    """Repository-relative posix paths of regular files under base. Uses os.walk (directory entries
    only, no per-file stat), which matters on slow network/VM-shared folders."""
    out = []
    for dirpath, _dirs, files in os.walk(base):
        rel = Path(dirpath).relative_to(ROOT).as_posix()
        out.extend(f"{rel}/{f}" for f in files)
    return out


def main() -> int:
    full = "--full" in sys.argv
    shard_k, shard_n = (int(x) for x in sys.argv[sys.argv.index("--shard") + 1].split("/")) if "--shard" in sys.argv else (0, 1)
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=16)
    problems, checks = [], 0

    def check(cond: bool, msg: str):
        nonlocal checks
        checks += 1
        if not cond:
            problems.append(msg)

    mpath = DS / "manifests" / "datasets.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    check(m.get("schema") == "visiontra.dataset_manifest/1", "manifest schema id")
    ids = [d.get("id") for d in m["datasets"]]
    check(len(ids) == len(set(ids)), "dataset ids unique")
    for d in m["datasets"]:
        i = d.get("id", "?")
        for k in REQUIRED:
            check(d.get(k) not in (None, ""), f"{i}: missing {k}")
        check(bool(re.fullmatch(r"VT-DS-\d{3}", i)), f"{i}: id format")
        check(d.get("status") in STATUSES, f"{i}: unknown status {d.get('status')}")
        lic = d.get("licence") or {}
        check(lic.get("status") in ("VERIFIED", "VERIFIED_WITH_NOTE", "UNRESOLVED"), f"{i}: licence status must be VERIFIED, VERIFIED_WITH_NOTE or UNRESOLVED")
        if lic.get("status", "").startswith("VERIFIED"):
            check(bool(lic.get("evidence")), f"{i}: licence marked verified without evidence")
        if d.get("intended_use_compatible"):
            check(lic.get("status", "").startswith("VERIFIED") and bool(lic.get("evidence")), f"{i}: compatible without verified licence evidence")
        raw = DS / "raw" / i
        disk_files = walk_files(raw) if raw.exists() else []
        has_files = bool(disk_files)
        if d["status"] in ACQUIRED:
            check(has_files, f"{i}: status {d['status']} but no files in {raw.relative_to(ROOT)}")
            check(lic.get("status", "").startswith("VERIFIED"), f"{i}: acquired without a verified licence")
            acq = d.get("acquisition") or {}
            fm = ROOT / (acq.get("file_manifest") or "missing")
            check(fm.exists(), f"{i}: per-file manifest missing")
            if fm.exists():
                with open(fm, newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                on_disk = set(disk_files)
                listed = {r["rel_path"] for r in rows}
                check(listed == on_disk, f"{i}: per-file manifest and disk differ (+{len(on_disk - listed)} unlisted, -{len(listed - on_disk)} missing)")
                check(len({r['file_id'] for r in rows}) == len(rows), f"{i}: duplicate file_id")
                def one(r):
                    p = ROOT / r["rel_path"]
                    try:
                        size = p.stat().st_size
                    except FileNotFoundError:
                        return [f"{r['rel_path']}: missing"]
                    errs = [] if size == int(r["bytes"]) else [f"{r['rel_path']}: size differs"]
                    if full and errs == [] and hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256"]:
                        errs.append(f"{r['rel_path']}: sha256 differs")
                    if r["label_path"] and r["label_path"].replace(f"datasets/raw/{i}/", "") not in names_on_disk:
                        errs.append(f"{r['rel_path']}: label path missing")
                    return errs
                names_on_disk = {x.replace(f"datasets/raw/{i}/", "") for x in on_disk}
                mine = [r for j, r in enumerate(rows) if j % shard_n == shard_k]
                for errs in pool.map(one, mine):
                    check(not errs, "; ".join(errs))
                meas = d.get("measured") or {}
                check(meas.get("images") == sum(1 for r in rows if r["kind"] == "image"), f"{i}: image count differs from per-file manifest")
                check(meas.get("invalid_images", 1) == 0 or d["status"] != "INTEGRITY_VERIFIED", f"{i}: INTEGRITY_VERIFIED with invalid images")
        else:
            check(not has_files, f"{i}: files on disk but status {d['status']}")
            check(d.get("measured") in (None, {}), f"{i}: measured counts without acquired files")
    # no credentials anywhere in metadata, manifests or logs
    for p in list((DS / "manifests").rglob("*.json")) + list((DS / "metadata").rglob("*.json")) + list((DS / "logs").rglob("*")):
        if p.is_file():
            check(not SECRET.search(p.read_text(encoding="utf-8", errors="replace")), f"{p.relative_to(ROOT)}: looks like it contains a credential")
    # raw data must not be tracked by git
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
    check("datasets/raw/" in gi and "datasets/archives/" in gi, ".gitignore must exclude datasets/raw/ and datasets/archives/")
    print(f"{checks} checks, {len(problems)} problem(s){' (full SHA-256 re-hash)' if full else ''}{f' [files shard {shard_k}/{shard_n}]' if shard_n > 1 else ''}")
    for p in problems[:50]:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
