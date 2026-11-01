"""Train a detector from a configuration (never touches the test split).

    python scripts/ml/train_detector.py ml/configs/detection/VT-EXP-001.json
    python scripts/ml/train_detector.py CONFIG --smoke 64 32 1      # pipeline check: 64 train, 32 valid images, 1 epoch
    python scripts/ml/train_detector.py CONFIG --resume experiments/VT-EXP-001/<run_id>

Every run gets a new directory (see visentra_ml/detection/engine.py). SIGTERM/Ctrl-C stops after the
current batch, saves a resumable checkpoint and records the run as 'interrupted'.
"""
import argparse
import json
import sys

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection import engine as E


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config")
    ap.add_argument("--smoke", nargs=3, type=int, metavar=("TRAIN", "VALID", "EPOCHS"))
    ap.add_argument("--resume", metavar="RUN_DIR")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    if a.resume:
        run_dir = C.resolve(a.resume, must_exist=True, writable=True)
        saved = json.loads((run_dir / "config.json").read_text())
        if C.digest(saved) != C.digest(cfg):
            print("refusing to resume: the configuration differs from the one the run started with")
            return 2
        rec = E.train(cfg, run_dir=run_dir, resume=True)
    else:
        rec = E.train(cfg, smoke=dict(zip(("train", "valid", "epochs"), a.smoke)) if a.smoke else None)
    print(json.dumps({k: rec.get(k) for k in ("run_id", "status", "epochs_completed", "best", "duration_seconds")}, default=str))
    return 0 if rec.get("status") == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())
