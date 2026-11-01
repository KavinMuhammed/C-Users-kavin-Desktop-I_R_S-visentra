"""Run a packaged detector on one image and print the VisionTra detection output contract
(visiontra.detection_output/1). Development tool for Phase 8 integration — not a production API.

    python scripts/ml/predict_image.py models/detection/VT-DET-0001 path/to/image.jpg [--ref my-image-1]
"""
import argparse
import json
import sys

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection.inference import Predictor


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle"); ap.add_argument("image"); ap.add_argument("--ref")
    a = ap.parse_args(argv)
    out = Predictor(C.resolve(a.bundle, must_exist=True)).predict_file(a.image, a.ref or a.image.split("/")[-1])
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
