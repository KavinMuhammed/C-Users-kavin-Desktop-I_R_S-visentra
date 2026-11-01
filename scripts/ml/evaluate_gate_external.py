"""Aircraft-relevance gate on external aircraft photographs it was never trained or calibrated on.

    python scripts/ml/evaluate_gate_external.py --gate VT-GATE-0002 --out docs/evidence/phase4/gate_external.json

Sets (every image shows an aircraft, so any 'irrelevant' is a false rejection and 'uncertain' is a review burden):
VT-CLEAN-001 (reviewed undamaged airliner noses, Wikimedia Commons), VT-HC-001 (damage photos, Wikimedia Commons/NARA),
and the Phase 4 user uploads (P4 manifest, regression only). The gate's own test split (non-aircraft admitted 13/197)
remains the false-acceptance measurement; nothing here changes the gate or its thresholds.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))
from visentra_ml.detection import domain_gate as DG  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="VT-GATE-0002")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    g = DG.Gate(ROOT / "models/gate" / a.gate, threads=4)
    sets = {
        "VT-CLEAN-001": [r["file"] for r in csv.DictReader(open(ROOT / "datasets/hard_cases/VT-CLEAN-001/manifest.csv", encoding="utf-8")) if r["status"] == "clean"],
        "VT-HC-001": [f"datasets/hard_cases/VT-HC-001/images/{Path(k).name}" for k in json.loads((ROOT / "datasets/hard_cases/VT-HC-001/annotations.json").read_text())["images"]],
        "P4-uploads": [f"data/objects/{c['object_key']}" for c in json.loads((ROOT / "docs/evidence/phase4/hard_cases_manifest.json").read_text(encoding="utf-8"))["cases"]
                       if not c.get("control")],
    }
    out = {"gate": a.gate, "threshold": g.threshold, "airplane_min": g.airplane_min, "uncertain_band": g.spec.get("uncertain"), "sets": {}}
    for name, files in sets.items():
        rows = []
        for rel in files:
            p = ROOT / rel
            if not p.is_file():
                rows.append({"file": rel, "decision": "MISSING"}); continue
            with Image.open(p) as im:
                im.load(); ev = g.evidence(im)
            state, reason = g.decide(ev)
            rows.append({"file": rel, "decision": state, "reason": reason, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in ev.items()}})
        c = Counter(r["decision"] for r in rows)
        n = sum(v for k, v in c.items() if k != "MISSING")
        out["sets"][name] = {"images": n, "counts": dict(c), "false_rejection_rate": round(c.get("irrelevant", 0) / n, 4) if n else None,
                             "uncertain_rate": round(c.get("uncertain", 0) / n, 4) if n else None,
                             "relevant_by_low_p_but_airplane": sum(1 for r in rows if r.get("decision") == "relevant" and r.get("p", 1) < g.threshold),
                             "rows": rows}
        print(name, out["sets"][name]["counts"], "false rejection", out["sets"][name]["false_rejection_rate"])
    Path(ROOT / a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
