"""Stage-by-stage trace of the hard-case manifest through the real gate and detector(s).

    python scripts/ml/trace_hard_cases.py --manifest docs/evidence/phase4/hard_cases_manifest.json \
        --out docs/evidence/phase4/hard_case_traces.json [--model VT-DET-0002] [--checkpoint NAME=path.pt@640 ...]

For every case the original object is read from the inspection store and its sha256 is checked. Recorded per model:
raw anchors, the maximum objectness and class probability per class (before any floor), candidates at the 0.001 floor
before and after class-wise NMS, how many survive the bundle's per-class thresholds, the top candidates (score =
objectness x class probability, with both parts), and the best-IoU candidate against the draft box of each expected
finding. Experiment checkpoints are scored with the stable bundle's thresholds so outcomes are comparable; that is a
diagnostic convention, not a calibrated operating point for them. Nothing is written to the database.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import torch
import torchvision
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import artifact as A  # noqa: E402
from visentra_ml.detection import domain_gate as DG  # noqa: E402
from visentra_ml.detection import geometry as G  # noqa: E402
from visentra_ml.detection import model as Mo  # noqa: E402
from visentra_ml.detection.inference import Predictor  # noqa: E402


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


class Runner:
    """A Predictor whose weights/input size may come from an experiment checkpoint (same preprocessing code path)."""

    def __init__(self, bundle: Path, ckpt: Path | None = None, size: int | None = None):
        self.p = Predictor(bundle, threads=4)
        self.source = str(bundle.relative_to(ROOT))
        if ckpt is not None:
            st = torch.load(ckpt, map_location="cpu", weights_only=True)
            m = Mo.build(self.p.meta["variant"], len(self.p.meta["classes"]))
            m.load_state_dict(st.get("state_dict", st), strict=True); m.eval()
            self.p.model = m
            self.source = str(ckpt.relative_to(ROOT)) + f" (epoch {st.get('epoch')})"
            self.sha = A.sha256(ckpt)
        else:
            self.sha = A.sha256(bundle / "model.pt")
        if size:
            self.p.meta = {**self.p.meta, "input_size": [size, size]}

    @torch.inference_mode()
    def trace(self, im: Image.Image, expected: list[dict]) -> dict:
        meta = self.p.meta
        classes, th = meta["classes"], meta["thresholds"]["score_threshold"]
        t0 = time.perf_counter()
        x, prep, info = self.p.preprocess(im)
        raw = self.p.model(x).float()[0]                       # [A, 5 + C]
        ms = (time.perf_counter() - t0) * 1000
        obj, cls = raw[:, 4], raw[:, 5:]
        score_all = obj[:, None] * cls
        floor = 0.001
        pre_nms = int((score_all.max(1).values >= floor).sum())
        # the production postprocess (Mo.postprocess) with anchor indices kept, so objectness and class probability are exact
        box = torch.stack([raw[:, 0] - raw[:, 2] / 2, raw[:, 1] - raw[:, 3] / 2, raw[:, 0] + raw[:, 2] / 2, raw[:, 1] + raw[:, 3] / 2], 1)
        cconf, cidx = cls.max(1)
        sc = obj * cconf
        idx = torch.nonzero(sc >= floor).flatten()
        keep = idx[torchvision.ops.batched_nms(box[idx], sc[idx], cidx[idx], meta["thresholds"]["nms_iou"])][:100] if idx.numel() else idx
        ref = Mo.postprocess(raw[None], len(classes), score_floor=floor, nms_iou=meta["thresholds"]["nms_iou"], max_det=100)[0]
        assert ref.shape[0] == keep.numel() and torch.allclose(ref[:, 4], sc[keep]), "trace diverged from production postprocess"
        W, H = info["width"], info["height"]
        cands = []
        for a in keep.tolist():
            c, s = int(cidx[a]), float(sc[a])
            r = G.restore(box[a].tolist(), prep)
            if not r:
                continue
            n = r["normalised"]
            cands.append({"class": classes[c], "score": round(s, 5), "objectness": round(float(obj[a]), 5),
                          "class_prob": round(float(cls[a, c]), 5), "class_probs": {k: round(float(cls[a, j]), 4) for j, k in enumerate(classes)},
                          "box_norm_xyxy": [round(n["x"], 4), round(n["y"], 4), round(n["x"] + n["w"], 4), round(n["y"] + n["h"], 4)],
                          "retained": s >= th[classes[c]]})
        exp_match = []
        for e in expected:
            best = max(((iou(c["box_norm_xyxy"], e["box_norm_xyxy"]), i) for i, c in enumerate(cands)), default=(0.0, None))
            any_cls = best[1]
            same = [(iou(c["box_norm_xyxy"], e["box_norm_xyxy"]), i) for i, c in enumerate(cands) if c["class"] == e["class"]]
            bs = max(same, default=(0.0, None))
            exp_match.append({"expected_class": e["class"], "best_iou_any_class": round(best[0], 3),
                              "best_any": {**cands[any_cls], "rank": any_cls + 1} if any_cls is not None else None,
                              "best_iou_same_class": round(bs[0], 3),
                              "best_same": {**cands[bs[1]], "rank": bs[1] + 1} if bs[1] is not None else None,
                              "outcome": ("detected" if bs[1] is not None and bs[0] >= 0.3 and cands[bs[1]]["retained"]
                                          else "localised_below_threshold" if bs[1] is not None and bs[0] >= 0.3
                                          else "misclassified" if any_cls is not None and best[0] >= 0.3 and cands[any_cls]["retained"]
                                          else "missed")})
        return {"model": self.source, "weights_sha256": self.sha, "input_size": meta["input_size"],
                "preprocess": {k: prep[k] for k in ("r_x", "pad_x", "pad_y")}, "oriented_size": [W, H],
                "raw_anchors": int(raw.shape[0]), "max_objectness": round(float(obj.max()), 5),
                "max_class_prob": {k: round(float(cls[:, j].max()), 5) for j, k in enumerate(classes)},
                "max_score": {k: round(float(score_all[:, j].max()), 5) for j, k in enumerate(classes)},
                "candidates_pre_nms_at_0.001": pre_nms, "candidates_post_nms": len(cands),
                "retained_at_thresholds": sum(c["retained"] for c in cands), "thresholds": th,
                "retained": [c for c in cands if c["retained"]], "top_candidates": cands[:8],
                "expected_match": exp_match, "ms": round(ms, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="VT-DET-0002")
    ap.add_argument("--gate", default="VT-GATE-0002")
    ap.add_argument("--objects", default="data/objects")
    ap.add_argument("--checkpoint", action="append", default=[], help="NAME=path/to/best.pt@SIZE (stable thresholds reused)")
    ap.add_argument("--also-size", type=int, action="append", default=[], help="extra input sizes for the stable bundle (diagnostic)")
    a = ap.parse_args()
    man = json.loads((ROOT / a.manifest).read_text(encoding="utf-8"))
    bundle = ROOT / "models/detection" / a.model
    runners = {a.model: Runner(bundle)}
    for s in a.also_size:
        runners[f"{a.model}@{s}"] = Runner(bundle, size=s)
    for spec in a.checkpoint:
        name, rest = spec.split("=", 1)
        path, size = rest.rsplit("@", 1)
        runners[name] = Runner(bundle, ROOT / path, int(size))
    gate = DG.Gate(ROOT / "models/gate" / a.gate, threads=4)
    out = {"schema": "visiontra.hard_case_trace/1", "manifest": a.manifest, "gate": a.gate,
           "runners": {k: {"source": r.source, "weights_sha256": r.sha, "input_size": r.p.meta["input_size"]} for k, r in runners.items()},
           "cases": []}
    for c in man["cases"]:
        p = ROOT / a.objects / c["object_key"]
        rec = {"case_id": c["case_id"], "filename": c["filename"]}
        if not p.is_file():
            out["cases"].append({**rec, "status": "MISSING_ORIGINAL", "path": str(p)}); continue
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        rec["sha256_ok"] = sha == c["sha256"]
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            rec.update(format=im.format, mode=im.mode, size=list(im.size), bytes=len(data),
                       exif_orientation=(im.getexif() or {}).get(0x0112, 1))
            t0 = time.perf_counter()
            ev = gate.evidence(im)
            state, reason = gate.decide(ev)
            rec["gate"] = {"decision": state, "reason": reason, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in ev.items()},
                           "threshold": gate.threshold, "airplane_min": gate.airplane_min, "uncertain_band": gate.spec.get("uncertain"),
                           "ms": round((time.perf_counter() - t0) * 1000, 1)}
            rec["models"] = {k: r.trace(im, c.get("expected_core") or []) for k, r in runners.items()}
        out["cases"].append(rec)
        print(c["case_id"], rec["gate"]["decision"], {k: (v["retained_at_thresholds"], [m["outcome"] for m in v["expected_match"]]) for k, v in rec["models"].items()})
    Path(ROOT / a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
