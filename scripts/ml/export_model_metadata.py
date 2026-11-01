"""Package a completed, evaluated run as an immutable model bundle under models/detection/<model_id>/.

    python scripts/ml/export_model_metadata.py --run experiments/VT-EXP-001/<run_id> --model-id VT-DET-0001 \
        --limitations docs/MODEL_CARD.md

The readiness level is computed from the recorded evidence (visentra_ml/detection/readiness.py).
Classes are reported as 'experimental' unless the level is CANDIDATE_FOR_INTEGRATION.
"""
import argparse
import json
import sys

import torch

import _common  # noqa: F401
from visentra_ml.detection import artifact as A
from visentra_ml.detection import config as C
from visentra_ml.detection import data as Dt
from visentra_ml.detection import readiness as R

LIMITATIONS = [
    "Single class: 'dent' only. Crack and corrosion are not supported: their source mappings (VT-DS-001 'crack', VT-DS-002 'Rust') are UNRESOLVED in CMAP-001.",
    "The 'dent' mapping itself is PROVISIONAL_ACCEPTED (agent spot check, no expert sign-off); source labels cover hail dents, impact damage and buckling without severity.",
    "Trained and evaluated on VT-DS-001 only (Innovation Hangar v2, Roboflow export, 640×640 stretched images; photographer and aircraft identity undocumented).",
    "The test split is a held-out phone-photo session (2023-05-12), independent of the training sessions, but very likely the same aircraft collection: NOT an aircraft-independent evaluation.",
    "No verified defect-free (negative) images exist: false alarms on clean surfaces cannot be measured; 'no detection' never means 'no dent'.",
    "Not evaluated on drone footage, other aircraft types, other sites, weather or operational lighting.",
    "Decision support only. Scores are model confidence, not severity or airworthiness probability.",
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--limitations", help="JSON file with a list of known limitations (default: the VT-DET-0001 list)")
    ap.add_argument("--review-assisted-policy", metavar="DOC",
                    help="Phase 13 (AD-13-01): an EVALUATED_LIMITED model may serve operational, review-assisted analysis. Its classes are then "
                         "declared supported; DOC records the decision. Every prediction stays pending until an engineer decides it.")
    a = ap.parse_args(argv)
    run_dir = C.resolve(a.run, must_exist=True)
    cfg = json.loads((run_dir / "config.json").read_text()); rec = json.loads((run_dir / "run.json").read_text())
    env = json.loads((run_dir / "environment.json").read_text())
    th = json.loads((run_dir / "thresholds.json").read_text()) if (run_dir / "thresholds.json").is_file() else None
    tests = sorted((run_dir / "evaluations").glob("test-*")) if (run_dir / "evaluations").exists() else []
    vals = sorted((run_dir / "evaluations").glob("valid-*")) if (run_dir / "evaluations").exists() else []
    test = json.loads((tests[0] / "metrics.json").read_text()) if tests else None   # the FIRST test evaluation is the one that counts
    mapping = Dt.load_mapping(cfg); classes = mapping["targets"]
    items, _ = Dt.load_items(cfg, "test", mapping, subset=cfg["dataset"]["test_subset"])
    ev = {"data_ready": True, "run_completed": rec.get("status") == "completed" and not rec.get("smoke"),
          "thresholds_from_validation": bool(th and th.get("selected_on") == "valid"), "final_test_evaluation": bool(test),
          "test_split_digest_matches": bool(test and test["split_digest"] == Dt.split_digest(items)),
          "per_class_metrics": bool(test and all(test["metrics"]["per_class_ap"][c]["AP50"] is not None for c in classes)),
          "limitations_documented": True, "targets_preregistered": None, "targets_met": None, "missed_defect_review_done": False, "dataset_licence_cleared": False}
    level = R.decide(ev)
    review_assisted = bool(a.review_assisted_policy) and level["level"] in ("EVALUATED_LIMITED", "CANDIDATE_FOR_INTEGRATION")
    limitations = json.loads(open(a.limitations, encoding="utf-8").read()) if a.limitations else LIMITATIONS
    lic = "; ".join(f"{d}: CC BY 4.0, attribution required" for d in cfg["dataset"]["datasets"]) + " — see DATASET_LICENCE_REGISTER"
    pre = cfg["model"].get("pretrained")
    blob = torch.load(run_dir / "checkpoints" / "best.pt", map_location="cpu", weights_only=True)
    meta = {
        "version": a.version, "task": "detection", "architecture": "YOLOX", "variant": cfg["model"]["variant"],
        "framework": {k: env.get(k) for k in ("torch", "torchvision", "numpy", "pillow", "yolox", "python")},
        "classes": classes, "supported_classes": classes if (level["level"] == "CANDIDATE_FOR_INTEGRATION" or review_assisted) else [],
        "experimental_classes": [] if (level["level"] == "CANDIDATE_FOR_INTEGRATION" or review_assisted) else classes,
        "use_policy": ({"mode": "operational_review_assisted", "decision": a.review_assisted_policy,
                        "rule": "Every prediction is stored as a pending AI proposal; an engineer confirms, rejects or reclassifies it before sign-off."}
                       if review_assisted else {"mode": "experimental"}),
        "input_size": cfg["model"]["input_size"], "preprocessing": cfg["model"]["preprocessing"],
        "taxonomy": mapping["taxonomy"], "class_mapping": {k: mapping[k] for k in ("mapping_id", "version", "sha256")},
        "dataset": {"prep_id": cfg["dataset"]["prep_id"], "datasets": cfg["dataset"]["datasets"], "splits": cfg["dataset"]["splits"],
                    "train_split_digest": rec["data"]["train"]["split_digest"], "valid_split_digest": rec["data"]["valid"]["split_digest"],
                    "test_split_digest": test["split_digest"] if test else None, "licence": lic},
        "training_summary": {"train_images": rec["data"]["train"].get("images"), "valid_images": rec["data"]["valid"].get("images"),
                             "test_images": test["metrics"].get("images") if test else None, "epochs_run": rec.get("epochs_completed") or rec.get("epochs")},
        "experiment": {"experiment_id": cfg["experiment_id"], "run_id": rec["run_id"], "config_sha256": rec["config_sha256"], "best": rec.get("best"),
                       "checkpoint_epoch": blob.get("epoch"), "source": env.get("source")},
        "thresholds": {"score_threshold": {c: th["classes"][c]["score_threshold"] for c in classes} if th else None,
                       "review_threshold": {c: th["classes"][c]["review_threshold"] for c in classes} if th else None,
                       "nms_iou": cfg["eval"]["nms_iou"], "match_iou": cfg["eval"]["match_iou"], "policy": th["policy"] if th else None},
        "evaluation": {"test": {k: test[k] for k in ("evaluated_at", "split", "subset", "split_digest")} | {"AP50": test["metrics"]["AP50"], "AP50_95": test["metrics"]["AP50_95"],
                                "per_class_at_threshold": test["metrics"]["per_class_at_threshold"]} if test else None,
                       "valid_evaluation": vals[0].name if vals else None},
        "weights_licence": {"model_code": "Apache-2.0 (YOLOX, vendored)", "pretrained_init": (pre or {}).get("licence", "none — random initialisation"),
                            "pretrained_source": (pre or {}).get("source_url"), "pretrained_sha256": (pre or {}).get("sha256"),
                            "fine_tuning_data": lic},
        "pretrained": rec.get("pretrained"), "readiness": level["level"], "readiness_reasons": level["reasons"], "readiness_evidence": ev,
        "known_limitations": limitations, "output_contract": "visiontra.detection_output/1 (ml/visentra_ml/detection/output.py)",
        "artifact_format": "torch_state_dict", "loading": "visentra_ml.detection.artifact.load(bundle_dir) — torch.load(weights_only=True)",
    }
    extra = {"config.json": run_dir / "config.json", "environment.json": run_dir / "environment.json", "run.json": run_dir / "run.json"}
    if th:
        extra["thresholds.json"] = run_dir / "thresholds.json"
    if test:
        extra["evaluation_test.json"] = tests[0] / "metrics.json"
    if vals:
        extra["evaluation_valid.json"] = vals[0] / "metrics.json"
    d = A.package(a.model_id, blob["state_dict"], meta, out_root=C.resolve("models/detection", writable=True), extra_files=extra)
    print(json.dumps({"bundle": str(d.relative_to(C.ROOT)), "readiness": level}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
