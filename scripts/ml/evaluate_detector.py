"""Evaluate a training run's best checkpoint.

    # 1) validation: metrics + operating thresholds (frozen into <run>/thresholds.json)
    python scripts/ml/evaluate_detector.py --run experiments/VT-EXP-001/<run_id> --split valid
    # 2) the single final held-out test evaluation (needs thresholds.json; refuses a repeat)
    python scripts/ml/evaluate_detector.py --run experiments/VT-EXP-001/<run_id> --split test --final

Outputs go to <run>/evaluations/<split>-<UTC>/: metrics.json, errors.csv and diagnostics/ (images,
git-ignored). Optional: --probe-dataset VT-DS-002 counts detections on that dataset's test images,
which have no dent annotations (reported as counts only — they cannot be scored as errors).
"""
import argparse
import json
import sys
import time

import torch

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection import data as Dt
from visentra_ml.detection import engine as E
from visentra_ml.detection import evaluation as V


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--split", choices=("valid", "test"), required=True)
    ap.add_argument("--checkpoint", default="best", choices=("best", "last"))
    ap.add_argument("--final", action="store_true", help="required for the test split")
    ap.add_argument("--allow-repeat", metavar="REASON", help="record a reason to evaluate the test split again")
    ap.add_argument("--probe-dataset", action="append", default=[], metavar="VT-DS-###")
    ap.add_argument("--robustness", action="store_true", help="also run brightness/contrast/resolution probes")
    a = ap.parse_args(argv)
    run_dir = C.resolve(a.run, must_exist=True, writable=True)
    rec = __import__("json").loads((run_dir / "run.json").read_text())
    if rec.get("status") != "completed" or rec.get("smoke"):
        print(f"refusing: run status is {rec.get('status')!r}{' (smoke run)' if rec.get('smoke') else ''}; only completed, non-smoke runs are evaluated")
        return 2
    evals = run_dir / "evaluations"
    if a.split == "test":
        if not a.final:
            print("refusing: the test split is evaluated only with --final (once, after all choices are made on validation)")
            return 2
        if not (run_dir / "thresholds.json").is_file():
            print("refusing: thresholds.json missing — run the validation evaluation first")
            return 2
        prior = sorted(evals.glob("test-*")) if evals.exists() else []
        if prior and not a.allow_repeat:
            print(f"refusing: this run's test split was already evaluated ({prior[-1].name}); use --allow-repeat REASON")
            return 2
    model, cfg, rec, ck = V.load_run_model(run_dir, a.checkpoint)
    if torch.cuda.is_available():
        model = model.cuda()                                   # same results, much faster on a GPU
    mapping = Dt.load_mapping(cfg); classes = mapping["targets"]
    subset = cfg["dataset"]["valid_subset"] if a.split == "valid" else cfg["dataset"]["test_subset"]
    items, _ = Dt.load_items(cfg, a.split, mapping, subset=subset)
    ev, size = cfg["eval"], cfg["model"]["input_size"]
    t0 = time.time()
    gts, preds, sizes = E.predict_items(model, items, size, ev, num_classes=len(classes))
    infer_s = time.time() - t0
    stamp = V.utc().replace(":", "").replace("-", "")
    out = evals / f"{a.split}-{stamp}"; out.mkdir(parents=True)
    res = {"schema": "visiontra.detection_evaluation/1", "experiment_id": cfg["experiment_id"], "run_id": run_dir.name, "split": a.split, "subset": subset,
           "evaluated_at": V.utc(), "model_checkpoint": ck, "classes": classes, "split_digest": Dt.split_digest(items), "data": Dt.summarise(items, classes),
           "eval_config": {**ev, "input_size": size, "ap_reference": "pycocotools COCOeval (bbox, maxDets 100, IoU 0.50 and 0.50:0.95)"},
           "environment": E.environment("cpu"), "inference_seconds": round(infer_s, 1), "inference_ms_per_image": round(1000 * infer_s / max(1, len(items)), 1)}
    if a.split == "valid":
        th = V.select_thresholds(gts, preds, classes, ev["match_iou"])
        thr = {c: th["classes"][c]["score_threshold"] for c in classes}
        (run_dir / "thresholds.json").write_text(json.dumps({"selected_on": "valid", "evaluation": out.name, **th}, indent=1))
        res["threshold_selection"] = {c: {k: v for k, v in th["classes"][c].items() if k != "curve"} for c in classes}
        res["threshold_curve"] = {c: th["classes"][c]["curve"] for c in classes}
    else:
        th = json.loads((run_dir / "thresholds.json").read_text())
        thr = {c: th["classes"][c]["score_threshold"] for c in classes}
        res["thresholds_from"] = th["evaluation"]
        if a.allow_repeat:
            res["repeat_reason"] = a.allow_repeat
    res["metrics"] = E.evaluate_preds(gts, preds, sizes, classes, match_iou=ev["match_iou"], score_threshold=thr)
    rev = {c: json.loads((run_dir / "thresholds.json").read_text())["classes"][c]["review_threshold"] for c in classes}
    res["metrics_at_review_threshold"] = E.evaluate_preds(gts, preds, sizes, classes, match_iou=ev["match_iou"], score_threshold=rev)["per_class_at_threshold"]
    res["by_session"] = V.breakdown_by_session(gts, preds, sizes, classes, cfg, match_iou=ev["match_iou"], score_threshold=thr)
    err = V.error_analysis(gts, preds, sizes, classes, thr, ev["match_iou"])
    res["error_analysis"] = {k: v for k, v in err.items() if k != "rows"}
    V.write_errors_csv(err["rows"], out / "errors.csv")
    by_id = {i.file_id: i for i in items}
    res["diagnostics"] = V.draw_diagnostics(by_id, gts, preds, thr, classes, err["rows"], out / "diagnostics")
    if a.robustness:
        res["robustness"] = {}
        for kind in ("darker_40pct", "brighter_40pct", "low_contrast_50pct", "half_resolution"):
            g2, p2, s2 = V.predict_with(V.perturb_items(items, kind), model, items, size, ev, len(classes))
            m2 = E.evaluate_preds(g2, p2, s2, classes, match_iou=ev["match_iou"], score_threshold=thr)
            res["robustness"][kind] = {"AP50": m2["AP50"], "AP50_95": m2["AP50_95"], "at_threshold": m2["per_class_at_threshold"]}
    for ds_id in a.probe_dataset:
        pcfg = json.loads(json.dumps(cfg)); pcfg["dataset"]["datasets"] = [ds_id]
        rows = [r for r in Dt.read_split(pcfg, a.split) if (subset != "eval_representatives" or r.get("eval_representative") == "yes")]
        pitems = [Dt.Item(r["file_id"], r["dataset_id"], r["rel_path"], "", r.get("sha256", ""), r["file_id"], r.get("photo_group", ""), r.get("eval_representative", "")) for r in rows]
        _, pp, ps = E.predict_items(model, pitems, size, ev, num_classes=len(classes))
        counts = [sum(1 for c, s, b in pp[k] if s >= thr[classes[c]]) for k in ps]
        res.setdefault("probes", {})[ds_id] = {"images": len(ps), "images_with_detection": sum(1 for c in counts if c), "detections": sum(counts),
                                              "note": f"{ds_id} has no '{'/'.join(classes)}' annotations; these detections cannot be scored as true or false positives."}
    (out / "metrics.json").write_text(json.dumps(res, indent=1, default=str))
    m = res["metrics"]
    print(json.dumps({"split": a.split, "images": m["images"], "AP50": m["AP50"], "AP50_95": m["AP50_95"], "at_threshold": m["per_class_at_threshold"],
                      "out": str(out.relative_to(C.ROOT))}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
