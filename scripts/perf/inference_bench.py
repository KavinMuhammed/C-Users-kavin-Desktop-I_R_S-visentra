"""Phase 12: VT-DET-0001 inference latency by stage (preprocess / model / postprocess) on this machine's CPU.

Uses validation-split images only (never the held-out test split). Prints JSON with p50/p95 per stage.
    python scripts/perf/inference_bench.py --n 20 --threads 1
This measures speed only; it says nothing about detection quality.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ml")]
from PIL import Image  # noqa: E402

from visentra_ml.detection.inference import Predictor  # noqa: E402


def pct(xs, p):
    xs = sorted(xs); return xs[max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20); ap.add_argument("--threads", type=int, default=1)
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(open(ROOT / "datasets/splits/VT-PREP-001/valid.csv", newline="")) if r["dataset_id"] == "VT-DS-001"][: a.n]
    t0 = time.perf_counter(); pr = Predictor(ROOT / "models/detection/VT-DET-0001", threads=a.threads); load_ms = (time.perf_counter() - t0) * 1000
    st = {"preprocess_ms": [], "model_ms": [], "postprocess_ms": [], "total_ms": []}
    for k, r in enumerate(rows):
        im = Image.open(ROOT / r["rel_path"]); im.load()
        timing = {}
        t = time.perf_counter(); pr.predict(im, r["file_id"], timing); tot = (time.perf_counter() - t) * 1000
        if k == 0:
            continue                                   # first call warms caches
        for key in ("preprocess_ms", "model_ms", "postprocess_ms"):
            st[key].append(timing[key])
        st["total_ms"].append(tot)
    out = {"model": "VT-DET-0001", "images": len(rows) - 1, "threads": a.threads, "cpu_count": os.cpu_count(), "model_load_ms": round(load_ms),
           "stages": {k: {"p50": round(statistics.median(v), 1), "p95": round(pct(v, 95), 1)} for k, v in st.items()}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
