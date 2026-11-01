"""Controlled model-selection evaluation: one model + one inference mode, everything measured on validation-side data.

    python scripts/ml/evaluate_candidate.py --bundle models/detection/VT-DET-0002 [--run experiments/<EXP>/<run>]
        --mode full|tiles2|tiles2-dent|tiles3-dent [--out experiments/selection/<name>.json] [--probes probes.json]

Steps (the held-out test split is never read here):
1. Predict the validation split (886 images) at score floor 0.001 with the chosen inference mode.
2. COCO AP (AP50, AP50-95, per class) and validation-selected thresholds (score = argmax F2, review = argmax F1).
3. Per-class precision/recall at those thresholds, overall and by framing (whole/medium, partial, close-up — measured
   per photo group by scripts/ml/audit_scale_framing.py).
4. False alarms at those thresholds on images with no aircraft defects by construction: the VT-NEG-001/002 valid split
   (non-aircraft) and, when present, the reviewed clean-aircraft set datasets/hard_cases/VT-CLEAN-001.
5. Development hard cases (VT-HC-001 eval split + incident probes; already used for comparisons, so they are a
   regression reference, not an independent test) at the same thresholds.
6. Mean inference latency per image on this machine.
Modes: 'tilesN' = full image + NxN overlapping tiles (20 %), merged with class-wise NMS; 'tilesN-dent' = tiles contribute
dent boxes only (crack/corrosion come from the full image, unchanged).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image, ImageOps
from torchvision.ops import batched_nms

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import config as C  # noqa: E402
from visentra_ml.detection import data as Dt  # noqa: E402
from visentra_ml.detection import engine as E  # noqa: E402
from visentra_ml.detection import evaluation as V  # noqa: E402
import evaluate_hard_cases as H  # noqa: E402
import evaluate_tiled as T  # noqa: E402


def predict(P, im, mode, nms_iou, classes):
    """List of (cls_index, score, xyxy) in original-image pixels."""
    full = [(d["cls"], d["score"], d["xyxy"]) for d in P.raw_detections(im)[0]]
    if mode == "full":
        return full
    n = int(mode[5]); dent_only = mode.endswith("-dent")
    tiled = T.predict_tiled(P, im, n, 0.2, nms_iou)            # includes the full image pass, merged
    if not dent_only:
        return tiled
    di = classes.index("dent")
    keep = [p for p in full if p[0] != di] + [p for p in tiled if p[0] == di]
    if not keep:
        return []
    b = torch.tensor([p[2] for p in keep], dtype=torch.float32); s = torch.tensor([p[1] for p in keep]); c = torch.tensor([p[0] for p in keep])
    k = batched_nms(b, s, c, nms_iou)[:100]
    return [(int(c[i]), float(s[i]), b[i].tolist()) for i in k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default="models/detection/VT-DET-0002")
    ap.add_argument("--run")
    ap.add_argument("--mode", default="full", choices=["full", "tiles2", "tiles3", "tiles2-dent", "tiles3-dent"])
    ap.add_argument("--config", default="ml/configs/detection/VT-EXP-006.json", help="only for the validation item list")
    ap.add_argument("--probes")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out")
    ap.add_argument("--input-size", type=int, help="override the bundle's square input size (inference-resolution study)")
    a = ap.parse_args()
    P = H.load_predictor(ROOT / a.bundle, ROOT / a.run if a.run else None)
    if a.input_size:
        P.meta["input_size"] = [a.input_size, a.input_size]
    if torch.cuda.is_available():
        P.model = P.model.cuda(); _f = P.model.forward
        P.model.forward = lambda x, *r, **k: _f(x.cuda(), *r, **k).cpu()
    classes = P.meta["classes"]; nms = P.meta["thresholds"]["nms_iou"]; miou = P.meta["thresholds"]["match_iou"]
    cfg = json.loads((ROOT / a.config).read_text()); mp = Dt.load_mapping(cfg)
    assert mp["targets"] == classes, "class order differs between model and dataset mapping"
    items, _ = Dt.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    items = items[: a.limit] if a.limit else items
    grp = {r["file_id"]: f"{r['dataset_id']}|{r['photo_group']}" for r in csv.DictReader(open(ROOT / "datasets/splits/VT-PREP-001/valid.csv", encoding="utf-8"))}
    fram = json.loads((ROOT / "experiments/audit/scale_framing.json").read_text())["splits"]["valid"]["photo_groups_framing"]

    gts, preds, sizes, t_total = {}, {}, {}, 0.0
    for k, it in enumerate(items):
        with Image.open(C.resolve(it.rel_path)) as im:
            im.load(); W, H_ = ImageOps.exif_transpose(im).size
            t0 = time.perf_counter(); preds[it.file_id] = predict(P, im, a.mode, nms, classes); t_total += time.perf_counter() - t0
        sizes[it.file_id] = (W, H_)
        gts[it.file_id] = [(b.cls, [b.x1 * W, b.y1 * H_, b.x2 * W, b.y2 * H_]) for b in it.boxes]
        if (k + 1) % 200 == 0:
            print(f"  valid {k + 1}/{len(items)}", flush=True)
    sel = V.select_thresholds(gts, preds, classes, miou)
    base_th = P.meta["thresholds"]["score_threshold"]
    th = {c: sel["classes"][c]["score_threshold"] if sel["classes"][c]["score_threshold"] is not None else base_th[c] for c in classes}
    vm = E.evaluate_preds(gts, preds, sizes, classes, match_iou=miou, score_threshold=th)
    by_frame = {}
    for fr in ("whole_or_medium", "partial", "close_up"):
        ids = [i for i in gts if fram.get(grp.get(i, ""), {}).get("framing") == fr]
        if ids:
            m = E.evaluate_preds({i: gts[i] for i in ids}, {i: preds[i] for i in ids}, {i: sizes[i] for i in ids}, classes, match_iou=miou, score_threshold=th)
            by_frame[fr] = {"images": len(ids), "per_class_at_threshold": m["per_class_at_threshold"]}

    def false_alarms(rows):
        n_img, n_det, per_cls = 0, 0, {c: 0 for c in classes}
        for rel in rows:
            with Image.open(C.resolve(rel)) as im:
                im.load(); ps = predict(P, im, a.mode, nms, classes)
            hits = [p for p in ps if p[1] >= th[classes[p[0]]]]
            n_img += 1; n_det += len(hits)
            for p in hits:
                per_cls[classes[p[0]]] += 1
        return {"images": n_img, "detections_at_threshold": n_det, "per_image": round(n_det / max(1, n_img), 3),
                "per_class": per_cls}
    neg = [r["file"] for m in ("datasets/negatives/VT-NEG-001/manifest.csv", "datasets/negatives/VT-NEG-002/manifest.csv")
           for r in csv.DictReader(open(ROOT / m, encoding="utf-8")) if r["split"] == "valid" and r["aircraft"] == "no"]
    fa = {"VT-NEG valid (non-aircraft)": false_alarms(neg[: a.limit] if a.limit else neg)}
    clean = ROOT / "datasets/hard_cases/VT-CLEAN-001/manifest.csv"
    if clean.exists():
        fa["VT-CLEAN-001 (reviewed undamaged aircraft)"] = false_alarms([r["file"] for r in csv.DictReader(open(clean, encoding="utf-8")) if r["status"] == "clean"])

    # development hard cases at the validation-selected thresholds
    P.meta["thresholds"] = {**P.meta["thresholds"], "score_threshold": th}
    hc = []
    for c in H.cases(Path(a.probes) if a.probes else None):
        with Image.open(c["file"]) as im:
            im.load(); ps = [(classes[p[0]], p[1], p[2]) for p in predict(P, im, a.mode, nms, classes)]
        g_out = []
        for g in c["boxes"]:
            x1, y1, x2, y2 = g["xyxy"]
            iou_s = max((p[1] for p in ps if p[0] == g["cls"] and H.iou(g["xyxy"], p[2]) >= 0.3), default=0.0)
            ctr = max((p[1] for p in ps if p[0] == g["cls"] and x1 <= (p[2][0] + p[2][2]) / 2 <= x2 and y1 <= (p[2][1] + p[2][3]) / 2 <= y2), default=0.0)
            g_out.append({"cls": g["cls"], "iou_score": round(iou_s, 4), "centre_score": round(ctr, 4), "detected_iou": iou_s >= th[g["cls"]], "detected_centre": ctr >= th[g["cls"]]})
        unmatched = sum(1 for p in ps if p[1] >= th[p[0]] and all(H.iou(p[2], g["xyxy"]) < 0.1 for g in c["boxes"]))
        hc.append({"id": c["id"], "source": c["source"], "framing": c.get("framing"), "gt": g_out, "unmatched": unmatched})
    res = {"schema": "visiontra.candidate_evaluation/1", "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "model": P.meta.get("model_id"), "bundle": a.bundle, "run": a.run, "mode": a.mode, "input_size": P.meta["input_size"],
           "device": "cuda" if torch.cuda.is_available() else "cpu",
           "valid": {"images": len(items), "split_digest": Dt.split_digest(items), "AP50": vm["AP50"], "AP50_95": vm["AP50_95"],
                     "per_class_ap": {c: {k: vm["per_class_ap"][c].get(k) for k in ("AP50", "AP50_95", "AP_small", "AP_medium", "AP_large")} for c in classes},
                     "thresholds": {c: {"score": th[c], "review": sel["classes"][c]["review_threshold"]} for c in classes},
                     "per_class_at_threshold": vm["per_class_at_threshold"], "by_framing": by_frame,
                     "latency_ms_per_image": round(1000 * t_total / max(1, len(items)), 1)},
           "false_alarms": fa,
           "dev_hard_cases": {"role": "development regression reference (already used for comparisons) - not an independent test",
                              "boxes": sum(len(r["gt"]) for r in hc), "detected_iou": sum(g["detected_iou"] for r in hc for g in r["gt"]),
                              "detected_centre": sum(g["detected_centre"] for r in hc for g in r["gt"]),
                              "unmatched": sum(r["unmatched"] for r in hc), "images": hc}}
    out = Path(a.out) if a.out else ROOT / "experiments/selection" / f"{(a.run or a.bundle).replace('/', '_')}__{a.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(res, indent=1, default=str))
    v = res["valid"]; d = res["dev_hard_cases"]
    r4 = lambda x: None if x is None else round(x, 4)  # noqa: E731
    print(json.dumps({"mode": a.mode, "AP50": r4(v["AP50"]), "AP50_95": r4(v["AP50_95"]),
                      "AP50_cls": {c: (round(v["per_class_ap"][c]["AP50"], 4) if v["per_class_ap"][c]["AP50"] is not None else None) for c in classes},
                      "thr": {c: v["thresholds"][c]["score"] for c in classes},
                      "P/R": {c: (r4(v["per_class_at_threshold"][c]["precision"]), r4(v["per_class_at_threshold"][c]["recall"])) for c in classes},
                      "neg_FA_per_img": fa["VT-NEG valid (non-aircraft)"]["per_image"], "dev": f"{d['detected_iou']}/{d['detected_centre']}/{d['boxes']} unmatched {d['unmatched']}",
                      "ms": v["latency_ms_per_image"]}))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
