"""Acquire a permitted dataset source listed in datasets/metadata/candidates.json.

    python scripts/datasets/acquire.py VT-ACQ-001            # download + verify + extract
    python scripts/datasets/acquire.py VT-ACQ-001 --dry-run  # show what would happen

Standard library only (Python 3.10+). Behaviour:
  * refuses to run unless every target dataset's licence status is VERIFIED / VERIFIED_WITH_NOTE
  * checks free disk space before downloading
  * downloads to <archive>.part and resumes with HTTP Range when the server supports it;
    an existing complete archive is reused (no repeated download)
  * records SHA-256 of the archive and saves the source's git tree listing as evidence
  * extracts only the configured sub-paths; rejects absolute paths, '..', links and devices
  * never overwrites: an existing identical file is skipped, a different one is an error
  * verifies every extracted file against the git blob SHA-1 published in the tree listing
  * appends JSON lines to datasets/logs/acquisition.jsonl (no credentials are ever logged)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(os.environ.get("VISIONTRA_ROOT") or Path(__file__).resolve().parents[2])  # override for tests
DS = ROOT / "datasets"
UA = "VisionTra-dataset-acquisition/1.0 (+research; respects source terms)"
OK_LICENCE = {"VERIFIED", "VERIFIED_WITH_NOTE"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(event: str, **kw) -> None:
    (DS / "logs").mkdir(parents=True, exist_ok=True)
    rec = {"at": now(), "event": event, **kw}
    with open(DS / "logs" / "acquisition.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"[{rec['at']}] {event} " + " ".join(f"{k}={v}" for k, v in kw.items() if k not in ("tool",)))


def relpath(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(p)  # outside the repository (e.g. VISIONTRA_ARCHIVE_DIR)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()   # git object id, not a security hash


def http_get(url: str, headers: dict | None = None, timeout: int = 60):
    if not url.lower().startswith(("https://", "http://")):          # never file:, ftp: or custom schemes
        raise ValueError(f"only http(s) sources are fetched: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - http(s) only, checked above


def download_curl(url: str, dest: Path, retries: int = 3) -> dict:
    """curl-based download with stall detection (<1 kB/s for 60 s aborts) and Range resume when the
    server supports it. Downloads into a local staging directory (VISIONTRA_DL_STAGING, default: a
    temp dir) and then copies into place, because streaming directly onto some mounted/synced folders
    stalled in testing. Returns stats."""
    import subprocess
    import tempfile
    staging = Path(os.environ.get("VISIONTRA_DL_STAGING") or tempfile.gettempdir()) / "visiontra-dl"
    staging.mkdir(parents=True, exist_ok=True)
    part = staging / (dest.name + ".part")
    ver = subprocess.run(["curl", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        cmd = ["curl", "-sS", "-L", "--fail", "--speed-limit", "1000", "--speed-time", "60", "-A", UA, "-o", str(part), "-w", "%{http_code} %{size_download}"]
        if have:
            cmd[1:1] = ["-C", "-"]
        t0 = time.time()
        r = subprocess.run(cmd + [url], capture_output=True, text=True)
        out = (r.stdout or "").split()
        log("download_attempt", url=url, attempt=attempt, tool=ver, exit_code=r.returncode, http_status=out[0] if out else None,
            bytes_this_attempt=int(out[1]) if len(out) > 1 else None, resumed_from=have, seconds=round(time.time() - t0, 1), stderr=(r.stderr or "")[-300:])
        if r.returncode == 33 and have:  # server does not support ranges: restart from zero
            part.unlink()
            continue
        if r.returncode == 0 and part.parent.resolve() == dest.parent.resolve():
            part.rename(dest)
            return {"bytes": dest.stat().st_size, "attempts": attempt, "tool": ver}
        if r.returncode == 0:
            tmp = dest.with_name(dest.name + ".copying")
            shutil.copyfile(part, tmp)
            tmp.rename(dest)
            if sha256_file(dest) != sha256_file(part):
                dest.unlink()
                raise SystemExit("copy from staging produced a different file — aborting")
            part.unlink()
            return {"bytes": dest.stat().st_size, "attempts": attempt, "tool": ver}
        time.sleep(min(30, 2 ** attempt))
    raise SystemExit(f"download failed after {retries} attempts; partial file kept at {part}")


def download(url: str, dest: Path, retries: int = 3) -> dict:
    """Use curl when available (preferred), otherwise the urllib fallback below."""
    if shutil.which("curl"):
        return download_curl(url, dest, retries)
    return download_urllib(url, dest, retries)


def download_urllib(url: str, dest: Path, retries: int = 3) -> dict:
    """Resumable download into dest (via dest.part). Returns stats."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with http_get(url, headers, timeout=120) as r:
                status = r.status
                if have and status != 206:  # server ignored Range: restart cleanly
                    log("resume_unsupported", url=url, status=status, discarded_bytes=have)
                    part.unlink()
                    have = 0
                mode = "ab" if have else "wb"
                got = 0
                t0 = time.time()
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                log("download_chunk_complete", url=url, http_status=status, attempt=attempt, resumed_from=have, bytes=got, seconds=round(time.time() - t0, 1))
            part.rename(dest)
            return {"bytes": dest.stat().st_size, "attempts": attempt}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            log("download_error", url=url, attempt=attempt, error=f"{type(e).__name__}: {e}", partial_bytes=part.stat().st_size if part.exists() else 0)
            time.sleep(min(30, 2 ** attempt))
    raise SystemExit(f"download failed after {retries} attempts; partial file kept at {part}")


def safe_member(m: tarfile.TarInfo) -> PurePosixPath | None:
    p = PurePosixPath(m.name)
    if p.is_absolute() or ".." in p.parts:
        return None
    if not (m.isfile() or m.isdir()):
        return None  # links, devices, fifos are rejected
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("acquisition_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true", help="verify files already on disk against the git tree (no download, no extraction)")
    ap.add_argument("--shard", default="0/1", help="with --verify-only: verify only files with index %% N == K (K/N); run all shards then --aggregate N")
    ap.add_argument("--aggregate", type=int, default=0, help="with --verify-only: combine the latest results of N shards into extract_result events")
    ap.add_argument("--fast-resume", action="store_true", help="when resuming, skip existing files whose size matches without reading them; run --verify-only afterwards")
    ap.add_argument("--max-seconds", type=float, default=0, help="pause extraction after N seconds (resume by re-running; existing identical files are skipped)")
    a = ap.parse_args()
    cand = json.loads((DS / "metadata" / "candidates.json").read_text(encoding="utf-8"))
    acq = next((x for x in cand["acquisitions"] if x["id"] == a.acquisition_id), None)
    if not acq:
        raise SystemExit(f"unknown acquisition {a.acquisition_id}")
    byid = {c["id"]: c for c in cand["candidates"]}
    tool = {"python": sys.version.split()[0], "platform": platform.platform(), "script": "scripts/datasets/acquire.py"}
    for ex in acq["extract"]:
        lic = byid[ex["dataset"]]["licence"]["status"]
        if lic not in OK_LICENCE:
            log("refused_licence", acquisition=acq["id"], dataset=ex["dataset"], licence_status=lic)
            raise SystemExit(f"{ex['dataset']}: licence status {lic} does not permit acquisition")
    # VISIONTRA_ARCHIVE_DIR lets the (large) archive live outside the repository, e.g. on local disk
    # when the repository is on a mounted/synced folder where very large writes stall.
    archives = Path(os.environ.get("VISIONTRA_ARCHIVE_DIR") or DS / "archives")
    archive = archives / acq["archive_name"]
    free = shutil.disk_usage(DS if DS.exists() else ROOT).free
    log("start", acquisition=acq["id"], url=acq["url"], commit=acq.get("commit"), free_bytes=free, dry_run=a.dry_run, tool=tool)
    if free < 3 * 1024 ** 3:
        log("refused_disk", free_bytes=free)
        raise SystemExit("less than 3 GB free — not downloading")
    if a.dry_run:
        print(json.dumps(acq, indent=2))
        return 0
    archives.mkdir(parents=True, exist_ok=True)

    # 1) source tree listing (evidence + per-file blob hashes)
    tree_path = DS / "metadata" / f"{acq['id']}_git_tree.json"
    if not tree_path.exists():
        with http_get(acq["tree_url"]) as r:
            tree = json.load(r)
        if tree.get("truncated"):
            raise SystemExit("git tree listing truncated — cannot verify every file")
        tree_path.write_text(json.dumps(tree, indent=1), encoding="utf-8")
        log("tree_saved", path=str(tree_path.relative_to(ROOT)), entries=len(tree["tree"]))
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    blobs = {t["path"]: t for t in tree["tree"] if t["type"] == "blob"}

    if a.verify_only:
        from concurrent.futures import ThreadPoolExecutor
        tree_sha = hashlib.sha1(tree_path.read_bytes(), usedforsecurity=False).hexdigest()[:12]
        if a.aggregate:
            ev = [json.loads(l) for l in (DS / "logs" / "acquisition.jsonl").read_text(encoding="utf-8").splitlines()]
            ok_all = True
            for ex in acq["extract"]:
                shards = {}
                for e in ev:
                    if e.get("event") == "verify_shard" and e.get("dataset") == ex["dataset"] and e.get("tree") == tree_sha and e.get("of") == a.aggregate:
                        shards[e["shard"]] = e  # latest wins
                if len(shards) != a.aggregate:
                    raise SystemExit(f"{ex['dataset']}: only {len(shards)}/{a.aggregate} shards verified")
                res = {k: sum(x[k] for x in shards.values()) for k in ("verified", "mismatch", "missing")}
                res["unexpected_files"] = shards[0]["unexpected_files"]
                res["expected_from_tree"] = shards[0]["expected_total"]
                complete = res["missing"] == 0 and res["mismatch"] == 0 and res["unexpected_files"] == 0 and res["verified"] == res["expected_from_tree"]
                ok_all &= complete
                log("extract_result", dataset=ex["dataset"], mode="verify_only", shards=a.aggregate, complete=complete, **res)
            log("finish", acquisition=acq["id"], ok=ok_all, mode="verify_only")
            return 0 if ok_all else 2
        k, n = (int(x) for x in a.shard.split("/"))
        ok_all = True
        for ex in acq["extract"]:
            base = DS / "raw" / ex["dataset"]
            expected = sorted((kk, v) for kk, v in blobs.items() if kk.startswith(ex["prefix"]) and kk.lower().endswith(tuple(ex["include_ext"])))
            mine = [x for i, x in enumerate(expected) if i % n == k]

            def check(item):
                kk, v = item
                p = base / kk[len(ex["prefix"]):]
                try:
                    data = p.read_bytes()
                except FileNotFoundError:
                    return "missing", p
                return ("verified" if git_blob_sha1(data) == v["sha"] else "mismatch"), p
            res = {"verified": 0, "mismatch": 0, "missing": 0}
            with ThreadPoolExecutor(max_workers=int(os.environ.get("VISIONTRA_READ_THREADS", "16"))) as pool:
                for outcome, p in pool.map(check, mine):
                    res[outcome] += 1
                    if outcome == "mismatch":
                        log("blob_mismatch", path=relpath(p))
            listed = set()
            if base.exists():
                for dirpath, _, fnames in os.walk(base):
                    for fn in fnames:
                        listed.add(os.path.relpath(os.path.join(dirpath, fn), base).replace(os.sep, "/"))
            unexpected = len(listed - {kk[len(ex["prefix"]):] for kk, _ in expected})
            ok = res["mismatch"] == 0 and res["missing"] == 0 and unexpected == 0
            ok_all &= ok
            if n == 1:
                log("extract_result", dataset=ex["dataset"], mode="verify_only", complete=ok and res["verified"] == len(expected), expected_from_tree=len(expected), unexpected_files=unexpected, **res)
            else:
                log("verify_shard", dataset=ex["dataset"], tree=tree_sha, shard=k, of=n, expected_total=len(expected), unexpected_files=unexpected, **res)
        log("finish", acquisition=acq["id"], ok=ok_all, mode="verify_only", shard=a.shard)
        return 0 if ok_all else 2

    # 2) archive
    if archive.exists():
        log("archive_reused", path=relpath(archive), bytes=archive.stat().st_size)
    else:
        st = download(acq["url"], archive)
        log("archive_downloaded", path=relpath(archive), **st)
    digest = sha256_file(archive)
    side = archive.with_name(archive.name + ".sha256")
    if side.exists() and side.read_text().split()[0] != digest:
        log("archive_checksum_changed", previous=side.read_text().split()[0], now=digest)
        raise SystemExit("archive checksum differs from the recorded one — investigate before continuing")
    side.write_text(f"{digest}  {archive.name}\n")
    log("archive_sha256", sha256=digest)

    # 3) safe, non-overwriting extraction of configured prefixes + blob verification
    stats = {ex["dataset"]: {"written": 0, "skipped_identical": 0, "verified": 0, "mismatch": 0, "missing_in_tree": 0} for ex in acq["extract"]}
    rejected = 0
    t_start = time.time()
    from concurrent.futures import ThreadPoolExecutor, wait as fwait
    pool = ThreadPoolExecutor(max_workers=int(os.environ.get("VISIONTRA_WRITE_THREADS", "8")))
    pending = []

    def write_new(dest: Path, data: bytes) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        tmp.rename(dest)

    def drain(limit: int = 0) -> None:
        nonlocal pending
        while len(pending) > limit:
            done, _ = fwait(pending[:1] if limit else pending)
            for f in done:
                f.result()  # re-raise write errors
            pending = [f for f in pending if not f.done()]
    # one directory listing per folder instead of one stat per file (slow on network/synced folders)
    existing: dict[str, int] = {}
    for ex in acq["extract"]:
        for dirpath, _, fnames in os.walk(DS / "raw" / ex["dataset"]):
            for fn in fnames:
                fp = os.path.join(dirpath, fn)
                existing[fp] = os.stat(fp).st_size if not a.fast_resume else -1
    with tarfile.open(archive, "r:gz") as tf:
        for m in tf:
            if a.max_seconds and time.time() - t_start > a.max_seconds:
                drain()
                pool.shutdown()
                log("extract_paused", acquisition=acq["id"], after_seconds=round(time.time() - t_start), **{k: v["written"] + v["skipped_identical"] for k, v in stats.items()})
                return 3
            p = safe_member(m)
            if p is None:
                rejected += 1
                log("member_rejected", name=m.name, type=int(m.type) if isinstance(m.type, int) else str(m.type))
                continue
            if not m.isfile() or len(p.parts) < 2:
                continue
            repo_path = PurePosixPath(*p.parts[1:]).as_posix()  # strip "<repo>-<sha>/" root
            for ex in acq["extract"]:
                if not repo_path.startswith(ex["prefix"]) or not repo_path.lower().endswith(tuple(ex["include_ext"])):
                    continue
                rel = repo_path[len(ex["prefix"]):]
                dest = DS / "raw" / ex["dataset"] / rel
                # lexical containment check: members were already validated (no absolute paths, no '..',
                # no links), so resolving on disk (slow on network folders) adds nothing
                if not os.path.normpath(dest).startswith(os.path.normpath(DS / "raw" / ex["dataset"]) + os.sep):
                    log("member_rejected", name=m.name, reason="escapes target")
                    continue
                data = tf.extractfile(m).read()
                s = stats[ex["dataset"]]
                b = blobs.get(repo_path)
                if b is None:
                    s["missing_in_tree"] += 1
                elif git_blob_sha1(data) == b["sha"]:
                    s["verified"] += 1
                else:
                    s["mismatch"] += 1
                    log("blob_mismatch", path=repo_path)
                if str(dest) in existing:
                    if a.fast_resume or (existing[str(dest)] == len(data) and dest.read_bytes() == data):
                        s["skipped_identical"] += 1
                        continue
                    log("refused_overwrite", path=str(dest.relative_to(ROOT)))
                    raise SystemExit(f"refusing to overwrite different existing file {dest}")
                pending.append(pool.submit(write_new, dest, data))
                drain(64)
                s["written"] += 1
    drain()
    pool.shutdown()
    # 4) completeness: every tree blob under the prefixes must have been seen
    for ex in acq["extract"]:
        expected = [k for k in blobs if k.startswith(ex["prefix"]) and k.lower().endswith(tuple(ex["include_ext"]))]
        seen = stats[ex["dataset"]]["verified"] + stats[ex["dataset"]]["mismatch"]
        stats[ex["dataset"]]["expected_from_tree"] = len(expected)
        complete = seen == len(expected) and stats[ex["dataset"]]["mismatch"] == 0
        log("extract_result", dataset=ex["dataset"], complete=complete, **stats[ex["dataset"]])
    ok = all(s["mismatch"] == 0 and s["verified"] == s["expected_from_tree"] for s in stats.values())
    log("finish", acquisition=acq["id"], ok=ok, rejected_members=rejected)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
