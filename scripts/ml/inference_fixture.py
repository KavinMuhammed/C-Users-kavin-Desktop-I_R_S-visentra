"""Integration fixture for a packaged detector: model loading + deterministic inference, no metrics.

    python scripts/ml/inference_fixture.py check  models/detection/VT-DET-0001     # verify (default use)
    python scripts/ml/inference_fixture.py create models/detection/VT-DET-0001     # record (once per model)

The fixture (``ml/fixtures/<model_id>.inference_fixture.json``) lists
* 3 **synthetic** images generated in code (flat/gradient surfaces, a dark ellipse, and an EXIF
  orientation-6 image that exercises coordinate restoration), and
* 4 **validation-split** images (VT-PREP-001 ``valid.csv``, 2 with and 2 without a dent label), referenced
  by file id and sha256 only (no image is copied). Held-out **test** images are never used.

``check`` runs the bundle in two fresh processes and requires
1. bit-identical raw outputs in both processes (determinism), and
2. agreement with the recorded outputs (scores ±1e-4, boxes ±0.5 px, same status and count), on
3. the same inputs: validation files by the sha256 of their bytes; synthetic images by ``content_sha256``
   (mode, size, EXIF orientation and decoded pixels). The PNG encoder is lossless but its compressed bytes
   differ between zlib/libpng builds (Linux vs Windows), so file bytes cannot identify a generated image.
Nothing is evaluated against labels, so no metric is produced or tuned.
Exit 0 PASS, 1 FAIL, 2 UNVERIFIED (missing dependency or validation images absent).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import _common  # noqa: F401
from visentra_ml.detection import config as C

FIXTURE_DIR = "ml/fixtures"
VALID = "datasets/splits/VT-PREP-001/valid.csv"
TEST = "datasets/splits/VT-PREP-001/test.csv"


def synthetic_images():
    """Deterministic in-memory images (PNG round trip, so EXIF orientation is honoured)."""
    from PIL import Image, ImageDraw
    out = []
    a = Image.new("RGB", (800, 600))
    d = ImageDraw.Draw(a)
    for y in range(600):
        v = 90 + y * 80 // 600
        d.line([(0, y), (800, y)], fill=(v, v, v + 6))
    out.append(("synthetic-gradient", a, None))
    b = a.copy()
    ImageDraw.Draw(b).ellipse([330, 230, 470, 330], fill=(55, 55, 60))
    out.append(("synthetic-ellipse", b, None))
    c = Image.new("RGB", (640, 480), (150, 152, 158))
    ImageDraw.Draw(c).ellipse([60, 300, 200, 420], fill=(70, 70, 75))
    out.append(("synthetic-exif6", c, 6))
    res = []
    for ref, im, orient in out:
        buf = io.BytesIO()
        ex = Image.Exif()
        if orient:
            ex[0x0112] = orient
        im.save(buf, format="PNG", exif=ex.tobytes() if orient else None)
        res.append((ref, buf.getvalue()))
    return res


def content_sha256(data: bytes) -> str:
    """Encoding-independent identity of an image: mode, size, EXIF orientation and the decoded pixels."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        orient = (im.getexif() or {}).get(0x0112, 1) or 1
        h = hashlib.sha256(f"{im.mode}|{im.size[0]}x{im.size[1]}|o{orient}|".encode())
        h.update(im.tobytes())
    return h.hexdigest()


def same_input(ref: str, a: dict, b: dict) -> bool:
    """Validation files must be byte-identical. A synthetic image may be re-encoded differently by another
    platform's PNG encoder, so it must instead have identical decoded content (recorded content_sha256)."""
    if a["sha256"] == b["sha256"]:
        return True
    return ref.startswith("synthetic-") and bool(a.get("content_sha256")) and a["content_sha256"] == b.get("content_sha256")


def pick_validation(root: Path, n_with: int = 2, n_without: int = 2) -> list[dict]:
    test = set()
    with (root / TEST).open(newline="", encoding="utf-8") as f:
        test = {r["sha256"] for r in csv.DictReader(f)}
    rows = []
    with (root / VALID).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["sha256"] not in test and r.get("eval_representative") == "yes":
                rows.append(r)
    rows.sort(key=lambda r: r["file_id"])
    w = [r for r in rows if "dent" in (r["mapped_classes"] or "").split("|")][:n_with]
    wo = [r for r in rows if not r["mapped_classes"]][:n_without]
    return [{"ref": f"valid-{i + 1}", "file_id": r["file_id"], "rel_path": r["rel_path"], "sha256": r["sha256"],
             "labelled_dent": r in w} for i, r in enumerate(w + wo)]


CHILD = r"""
import hashlib, io, json, sys
sys.path.insert(0, sys.argv[2]); sys.path.insert(0, sys.argv[3])
from pathlib import Path
from PIL import Image
from visentra_ml.detection.inference import Predictor
import inference_fixture as F
spec = json.loads(Path(sys.argv[4]).read_text())
p = Predictor(Path(sys.argv[1]), threads=1)
res = {}
imgs = [(r, b) for r, b in F.synthetic_images()] + [(v["ref"], (Path(sys.argv[5]) / v["rel_path"]).read_bytes()) for v in spec["validation"]]
for ref, data in imgs:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        raw, prep, info = p.raw_detections(im)
        out = p.predict(im, ref)
    res[ref] = {"sha256": hashlib.sha256(data).hexdigest(), "content_sha256": F.content_sha256(data), "status": out["status"], "width": info["width"], "height": info["height"],
                "detections": [{"class": d["class_name"], "score": d["score"], "box": d["box_xyxy"], "low_confidence": d["low_confidence"]} for d in out["detections"]],
                "raw_top": [{"score": d["score"], "box": d["xyxy"]} for d in sorted(raw, key=lambda d: -d["score"])[:5]],
                "raw_count": len(raw), "raw_sha256": hashlib.sha256(repr([(d["cls"], d["score"], d["xyxy"]) for d in raw]).encode()).hexdigest()}
print(json.dumps({"model_id": p.meta["model_id"], "results": res}))
"""


def run_child(bundle: Path, spec_path: Path) -> dict:
    r = subprocess.run([sys.executable, "-c", CHILD, str(bundle), str(C.ROOT / "ml"), str(Path(__file__).parent), str(spec_path), str(C.ROOT)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def compare(rec: dict, got: dict, tol_s=1e-4, tol_b=0.5) -> list[str]:
    probs = []
    for ref, a in rec.items():
        b = got.get(ref)
        if b is None:
            probs.append(f"{ref}: missing"); continue
        if not same_input(ref, a, b):
            probs.append(f"{ref}: input differs" + (" (decoded content)" if ref.startswith("synthetic-") and a.get("content_sha256") else " (file bytes)")); continue
        if (a["status"], len(a["detections"]), a["width"], a["height"]) != (b["status"], len(b["detections"]), b["width"], b["height"]):
            probs.append(f"{ref}: status/count/size {a['status']},{len(a['detections'])} vs {b['status']},{len(b['detections'])}")
            continue
        for x, y in zip(a["detections"] + a["raw_top"], b["detections"] + b["raw_top"]):
            if abs(x["score"] - y["score"]) > tol_s or any(abs(u - v) > tol_b for u, v in zip(x["box"], y["box"])):
                probs.append(f"{ref}: {x} vs {y}"); break
    return probs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["check", "create"])
    ap.add_argument("bundle")
    a = ap.parse_args(argv)
    bundle = C.resolve(a.bundle, must_exist=True)
    meta = json.loads((bundle / "metadata.json").read_text())
    fx = C.ROOT / FIXTURE_DIR / f"{meta['model_id']}.inference_fixture.json"
    try:
        import torch  # noqa: F401
    except ImportError:
        print("UNVERIFIED  PyTorch is not installed in this environment (pip package 'torch'); model loading and inference not checked")
        return 2
    if a.action == "create":
        if fx.exists():
            print(f"REFUSED: {fx.relative_to(C.ROOT)} exists; fixtures are recorded once per model")
            return 1
        spec = {"schema": "visiontra.inference_fixture/1", "model_id": meta["model_id"],
                "model_sha256": meta["files"]["model.pt"]["sha256"], "metrics_computed": False,
                "note": "Integration fixture: loading and deterministic inference only. Synthetic images + validation-split images; no held-out test image. Not an evaluation.",
                "validation": pick_validation(C.ROOT)}
        fx.parent.mkdir(parents=True, exist_ok=True)
        fx.write_text(json.dumps(spec, indent=1))
        r1, r2 = run_child(bundle, fx), run_child(bundle, fx)
        same = all(r1["results"][k]["raw_sha256"] == r2["results"][k]["raw_sha256"] for k in r1["results"])
        if not same:
            fx.unlink()
            print("FAIL  two fresh processes disagree; fixture not recorded")
            return 1
        import platform
        import torch as T
        spec["recorded_with"] = {"python": platform.python_version(), "torch": T.__version__, "platform": platform.platform()}
        spec["results"] = r1["results"]
        fx.write_text(json.dumps(spec, indent=1) + "\n")
        print(f"recorded {len(spec['results'])} images → {fx.relative_to(C.ROOT)}")
        return 0
    spec = json.loads(fx.read_text())
    if spec["model_sha256"] != meta["files"]["model.pt"]["sha256"]:
        print("FAIL  fixture was recorded for different weights"); return 1
    missing = [v["rel_path"] for v in spec["validation"] if not (C.ROOT / v["rel_path"]).is_file()]
    if missing:
        print(f"UNVERIFIED  validation images absent: {missing[:2]}"); return 2
    r1, r2 = run_child(bundle, fx), run_child(bundle, fx)
    det = [k for k in r1["results"] if r1["results"][k]["raw_sha256"] != r2["results"][k]["raw_sha256"]]
    print(("PASS" if not det else "FAIL") + f"  deterministic: two fresh processes, {len(r1['results'])} images, bit-identical raw outputs"
          + (f" (differs: {det})" if det else ""))
    probs = compare(spec["results"], r1["results"])
    exact = sum(spec["results"][k]["raw_sha256"] == r1["results"][k]["raw_sha256"] for k in spec["results"])
    print(("PASS" if not probs else "FAIL") + f"  matches the recorded fixture (tolerance score 1e-4, box 0.5 px); bit-exact on {exact}/{len(spec['results'])}"
          + ("" if not probs else "\n  " + "\n  ".join(probs)))
    for k, v in r1["results"].items():
        top = v["raw_top"][0]["score"] if v["raw_top"] else None
        print(f"      {k:<20} {v['status']:<14} shown {len(v['detections'])}  raw {v['raw_count']}  top raw score {top if top is None else round(top, 4)}")
    print("No metrics computed (fixture only).")
    return 0 if not det and not probs else 1


if __name__ == "__main__":
    sys.exit(main())
