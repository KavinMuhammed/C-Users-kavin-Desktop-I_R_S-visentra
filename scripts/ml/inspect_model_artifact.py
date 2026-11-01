"""Verify a model bundle and prove it loads in a FRESH process.

    python scripts/ml/inspect_model_artifact.py models/detection/VT-DET-0001

1. Checks every sha256 in SHA256SUMS and the metadata (no torch needed).
2. Starts a new Python process that loads the bundle with the documented code path
   (visentra_ml.detection.artifact.load, torch.load(weights_only=True)), runs the model twice on a
   fixed synthetic input (not an aircraft image) and prints an output fingerprint.
3. Compares the two runs and the parent's own load (determinism / consistency).
If torch is not installed, step 2 is reported as UNVERIFIED with the missing dependency named
(exit 0, or exit 2 with --require-load so automation cannot mistake it for a pass).
"""
import argparse
import hashlib
import json
import subprocess
import sys

import _common  # noqa: F401
from visentra_ml.detection import artifact as A
from visentra_ml.detection import config as C

CHILD = r"""
import json, sys, hashlib
sys.path.insert(0, sys.argv[2])
import torch
from visentra_ml.detection import artifact as A, model as Mo
from pathlib import Path
m, meta = A.load(Path(sys.argv[1]))
g = torch.Generator().manual_seed(1234)
h, w = meta["input_size"]
x = torch.randint(0, 256, (1, 3, h, w), generator=g).float()
torch.set_num_threads(1)
with torch.no_grad():
    o1 = m(x); o2 = m(x)
fp = lambda t: hashlib.sha256(t.numpy().round(4).tobytes()).hexdigest()
det = Mo.postprocess(o1, len(meta["classes"]), score_floor=0.001, nms_iou=0.65, max_det=100)[0]
print(json.dumps({"loaded": True, "model_id": meta["model_id"], "classes": meta["classes"], "readiness": meta["readiness"],
                  "raw_shape": list(o1.shape), "fingerprint_1": fp(o1), "fingerprint_2": fp(o2), "identical_runs": bool(torch.equal(o1, o2)),
                  "detections_on_synthetic_input": int(det.shape[0]), "torch": torch.__version__}))
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    ap.add_argument("--require-load", action="store_true", help="exit 2 (not 0) when the load cannot be verified, e.g. without PyTorch")
    a = ap.parse_args(argv)
    b = C.resolve(a.bundle, must_exist=True)
    v = A.verify(b)
    print(("PASS" if v["ok"] else "FAIL") + "  checksums and metadata" + ("" if v["ok"] else ": " + "; ".join(v["problems"])))
    try:
        import torch  # noqa: F401
    except ImportError:
        print("UNVERIFIED  fresh-process load: PyTorch is not installed in this environment (pip package 'torch')")
        return 1 if not v["ok"] else (2 if a.require_load else 0)
    r = subprocess.run([sys.executable, "-c", CHILD, str(b), str(C.ROOT / "ml")], capture_output=True, text=True, timeout=600)
    if r.returncode:
        print("FAIL  fresh-process load:\n" + r.stderr[-2000:])
        return 1
    child = json.loads(r.stdout.strip().splitlines()[-1])
    print("PASS  fresh-process load: " + json.dumps(child))
    ok = v["ok"] and child["identical_runs"] and child["fingerprint_1"] == child["fingerprint_2"]
    print(("PASS" if child["identical_runs"] else "FAIL") + "  deterministic output on repeated inference")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
