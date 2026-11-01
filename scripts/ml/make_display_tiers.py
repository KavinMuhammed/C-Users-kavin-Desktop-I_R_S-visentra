"""Derive the UI's plain-language confidence tiers from a validation precision curve (experiments/confidence_tiers/<model>.json).

Score bands per class (display only — stored detections and the model's operating thresholds are NOT changed):
  [score_threshold, review_threshold), [review_threshold, likely_from), [likely_from, 1]
  where likely_from is the lowest score >= review_threshold with validation precision >= 0.5 over >= 20 boxes.
Each band is named by its measured validation hit rate: likely >= 50 %, possible 10-50 %, unlikely < 10 %.
Each tier carries its measured validation hit rate (fraction of boxes in the tier that matched a labelled defect, IoU >= 0.5).
    python scripts/ml/make_display_tiers.py experiments/confidence_tiers/VT-DET-0002.json models/detection/VT-DET-0002/display_tiers.json
"""
import json
import sys


def main(src, dst):
    d = json.load(open(src)); out = {"model_id": d["model"], "source": src, "split": d["split"], "images": d["images"], "match_iou": d["match_iou"],
                                      "rule": "likely only where validation precision >= 0.5 with >= 20 boxes; hit rate = matched boxes / boxes in the tier",
                                      "classes": {}}
    for c, v in d["classes"].items():
        cv = sorted(v["curve"], key=lambda x: x["score"])
        at = lambda t: min(cv, key=lambda x: abs(x["score"] - t))  # noqa: E731
        sc, rv = v["score_threshold"], v["review_threshold"]
        likely = next((x["score"] for x in cv if x["score"] >= rv and x["precision"] is not None and x["precision"] >= 0.5 and x["tp"] + x["fp"] >= 20), None)

        def band(lo, hi):
            a = at(lo); tp, fp = a["tp"], a["fp"]
            if hi is not None:
                b = at(hi); tp, fp = tp - b["tp"], fp - b["fp"]
            return {"from": lo, "to": hi, "boxes": tp + fp, "hit_rate": round(tp / (tp + fp), 3) if tp + fp else None}
        bands = [b for b in (band(sc, rv), band(rv, likely), band(likely, None) if likely else None) if b and b["boxes"]]
        for b in bands:   # the name follows the measured hit rate, not the threshold it came from
            b["tier"] = "likely" if (b["hit_rate"] or 0) >= 0.5 else "possible" if (b["hit_rate"] or 0) >= 0.1 else "unlikely"
        out["classes"][c] = {"bands": bands}
    json.dump(out, open(dst, "w"), indent=1); print(json.dumps(out["classes"], indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
