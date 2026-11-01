"""MLE-08: re-run the packaged model on the VALIDATION split and compare with the metrics recorded at
packaging time (evaluation_valid.json). Identical AP and TP/FP/FN counts = reproducible inference.

    python scripts/ml/verify_reproducibility.py models/detection/VT-DET-0001
"""
import json
import sys

import _common  # noqa: F401
from visentra_ml.detection import artifact as A
from visentra_ml.detection import config as C
from visentra_ml.detection import data as D
from visentra_ml.detection import engine as E


def main(argv=None):
    b = C.resolve((argv or sys.argv[1:])[0], must_exist=True)
    model, meta = A.load(b)
    cfg = json.loads((b / "config.json").read_text())
    rec = json.loads((b / "evaluation_valid.json").read_text())
    mp = D.load_mapping(cfg)
    items, _ = D.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    if D.split_digest(items) != rec["split_digest"]:
        print("FAIL  validation split digest differs from the recorded one"); return 1
    thr = meta["thresholds"]["score_threshold"]
    g, p, s = E.predict_items(model, items, meta["input_size"], cfg["eval"], num_classes=len(meta["classes"]))
    m = E.evaluate_preds(g, p, s, meta["classes"], match_iou=cfg["eval"]["match_iou"], score_threshold=thr)
    old = rec["metrics"]
    same = (m["AP50"] == old["AP50"] and m["AP50_95"] == old["AP50_95"]
            and all(m["per_class_at_threshold"][c][k] == old["per_class_at_threshold"][c][k] for c in meta["classes"] for k in ("tp", "fp", "fn")))
    print(json.dumps({"recorded": {"AP50": old["AP50"], "AP50_95": old["AP50_95"], "at_threshold": old["per_class_at_threshold"]},
                      "rerun": {"AP50": m["AP50"], "AP50_95": m["AP50_95"], "at_threshold": m["per_class_at_threshold"]}}, indent=1))
    print("PASS  identical metrics on re-run" if same else "FAIL  metrics differ on re-run")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
