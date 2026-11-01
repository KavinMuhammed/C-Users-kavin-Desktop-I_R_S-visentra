"""Validate a detection experiment configuration and run the pre-training readiness gate.

    python scripts/ml/validate_training_config.py ml/configs/detection/VT-EXP-001.json [--hash] [--no-images]

Writes experiments/<EXP>/readiness-<UTC>.json and exits 1 if the configuration is invalid or any
readiness condition fails. Each condition is reported separately (Phase 7 §4).
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection import data as D


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config")
    ap.add_argument("--hash", action="store_true", help="also re-hash every image against the manifest sha256 (slow)")
    ap.add_argument("--no-images", action="store_true", help="skip opening images (structure and labels only)")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = C.load(a.config)
    except C.ConfigError as e:
        print(f"CONFIG INVALID\n{e}")
        return 1
    rep = D.readiness(cfg, hash_files=a.hash, check_images=not a.no_images)
    rep["config"] = a.config
    rep["config_sha256"] = C.digest(cfg)
    rep["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for k, v in rep["conditions"].items():
        print(f"{'PASS' if v['ok'] else 'FAIL'}  {k}: {json.dumps(v['detail'], default=str)[:300]}")
    for s, v in rep.get("splits", {}).items():
        print(f"      {s}: {v['images']} images ({v['subset']}), {v['images_with_target']} with target, instances {v['instances']}, dropped {v['dropped_source_instances']}")
    print("READY" if rep["ready"] else "NOT READY")
    if not a.no_write:
        out = C.resolve(cfg["output_dir"], writable=True)
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"readiness-{rep['checked_at'].replace(':', '').replace('-', '')}.json"
        p.write_text(json.dumps(rep, indent=1, default=str))
        print(f"wrote {p.relative_to(C.ROOT)}")
    return 0 if rep["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
