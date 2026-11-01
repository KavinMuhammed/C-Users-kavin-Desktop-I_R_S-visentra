"""Development-only experimental inference with an allow-listed model (Phase 8 preparation).

    VISENTRA_ENV=development VISIONTRA_EXPERIMENTAL_DETECTION=1 \
      python scripts/ml/experimental_inference.py models/detection/VT-DET-0001 IMAGE_OR_FOLDER [...] [--out experiments/dev-inference/NAME]

Isolation rules (ml/visentra_ml/detection/experimental.py, docs/PHASE_8_EXPERIMENTAL_INFERENCE.md):
* refused unless the flag is set in a development/test environment (default: off);
* only allow-listed experimental models (VT-DET-0001, dent);
* refuses any image whose sha256 is in a held-out **test** split manifest (datasets/splits/*/test.csv), so
  the test set cannot be re-scored or tuned against through this tool;
* writes only ``visiontra.experimental_detection_output/1`` JSON files under ``experiments/dev-inference/``
  (git-ignored). It never writes to the database, inspections, findings, reports or datasets/.
* computes no metrics.

Exit codes: 0 ok, 2 usage, 3 mode disabled, 4 model rejected, 5 held-out test image refused.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection import experimental as X

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
OUT_ROOT = "experiments/dev-inference"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def held_out_test_hashes(root: Path) -> dict[str, str]:
    """sha256 → manifest for every file in a held-out test split manifest."""
    out = {}
    for m in sorted((root / "datasets/splits").glob("*/test.csv")):
        with m.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("sha256"):
                    out[row["sha256"]] = str(m.relative_to(root))
    return out


def collect(paths: list[str]) -> list[Path]:
    files = []
    for s in paths:
        p = Path(s)
        if p.is_dir():
            files += sorted(q for q in p.rglob("*") if q.suffix.lower() in EXTS and q.is_file())
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(s)
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", help=f"repository-relative output folder under {OUT_ROOT}/")
    a = ap.parse_args(argv)

    try:
        env = X.check_enabled()
    except X.ExperimentalModeDisabled as e:
        print(f"REFUSED ({e.code}): {e}", file=sys.stderr)
        return 3
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_rel = a.out or f"{OUT_ROOT}/{stamp}"
    try:
        out_dir = C.resolve(out_rel, writable=True)
    except C.ConfigError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if (C.ROOT / OUT_ROOT).resolve() not in [out_dir, *out_dir.parents] or out_dir == (C.ROOT / OUT_ROOT).resolve():
        print(f"REFUSED: output must be a folder inside {OUT_ROOT}/", file=sys.stderr)
        return 2
    try:
        files = collect(a.images)
    except FileNotFoundError as e:
        print(f"not found: {e}", file=sys.stderr)
        return 2
    if not files:
        print("no images given", file=sys.stderr)
        return 2
    test = held_out_test_hashes(C.ROOT)
    hashes = {f: sha256(f) for f in files}
    hit = [(f, test[h]) for f, h in hashes.items() if h in test]
    if hit:
        for f, m in hit[:10]:
            print(f"REFUSED (held-out test image, {m}): {f}", file=sys.stderr)
        print(f"{len(hit)} image(s) belong to a held-out test split; nothing was run.", file=sys.stderr)
        return 5

    from visentra_ml.detection.inference import Predictor   # torch only needed past the guards
    pred = Predictor(C.resolve(a.bundle, must_exist=True))
    try:
        X.check_model(pred.meta)
    except X.ExperimentalModelRejected as e:
        print(f"REFUSED ({e.code}): {e}", file=sys.stderr)
        return 4
    out_dir.mkdir(parents=True, exist_ok=False)
    items = []
    for i, f in enumerate(files, 1):
        ref = f"dev-{i:04d}"
        o = X.label(pred.predict_file(f, ref), pred.meta)
        o["input"]["sha256"] = hashes[f]
        (out_dir / f"{ref}.json").write_text(json.dumps(o, indent=1), encoding="utf-8")
        items.append({"ref": ref, "file": f.name, "sha256": hashes[f], "status": o["status"], "detections": len(o["detections"])})
        print(f"{ref}  {o['status']:<14} {len(o['detections']):>3} experimental box(es)  {f.name}")
    run = {"schema": "visiontra.experimental_inference_run/1", "created_at": stamp, "environment": env,
           "model_id": pred.meta["model_id"], "readiness": pred.meta["readiness"], "banner": X.BANNER,
           "operational_use": False, "findings_eligible": False, "report_eligible": False, "metrics_computed": False,
           "held_out_test_guard": {"manifests": sorted(set(test.values())), "hashes_checked": len(test)}, "items": items}
    (out_dir / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    print(f"\n{X.BANNER}\n{len(items)} image(s) → {out_dir.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
