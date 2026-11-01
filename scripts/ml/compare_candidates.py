"""Tabulate controlled selection evaluations (experiments/selection/*.json) side by side — validation-side data only.

    python scripts/ml/compare_candidates.py [files...] > table.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLS = ("crack", "dent", "corrosion")


def f(x, n=3):
    return "—" if x is None else f"{x:.{n}f}"


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted((ROOT / "experiments/selection").glob("*.json"))
    R = [(p, json.loads(p.read_text())) for p in files]
    names = [f"{r['model']} · {r['mode']}" for _, r in R]
    out = ["| Metric (validation, 886 images) | " + " | ".join(names) + " |", "|---" * (len(R) + 1) + "|"]

    def row(label, fn):
        out.append(f"| {label} | " + " | ".join(fn(r) for _, r in R) + " |")
    row("AP50 / AP50-95", lambda r: f"{f(r['valid']['AP50'])} / {f(r['valid']['AP50_95'], 4)}")
    for c in CLS:
        row(f"{c} AP50 / AP50-95", lambda r, c=c: f"{f(r['valid']['per_class_ap'][c]['AP50'])} / {f(r['valid']['per_class_ap'][c]['AP50_95'])}")
    for c in CLS:
        row(f"{c} threshold (F2, validation)", lambda r, c=c: f(r['valid']['thresholds'][c]['score']))
    for c in CLS:
        def pr(r, c=c):
            m = r['valid']['per_class_at_threshold'][c]
            return f"P {f(m['precision'])} · R {f(m['recall'])} · TP {m['tp']} FP {m['fp']} FN {m['fn']}"
        row(f"{c} at threshold", pr)
    for fr in ("whole_or_medium", "partial", "close_up"):
        def fd(r, fr=fr):
            b = r['valid']['by_framing'].get(fr)
            if not b:
                return "—"
            m = b['per_class_at_threshold']['dent']
            return f"P {f(m['precision'])} · R {f(m['recall'])} ({m['tp']}/{m['tp'] + m['fn']})"
        row(f"dent, {fr.replace('_', ' ')} framing", fd)
    for k in ("VT-NEG valid (non-aircraft)", "VT-CLEAN-001 (reviewed undamaged aircraft)"):
        def fa(r, k=k):
            v = r['false_alarms'].get(k)
            return "not measured" if not v else f"{v['per_image']:.2f}/img ({v['detections_at_threshold']} on {v['images']}; dent {v['per_class']['dent']})"
        row(f"false alarms: {k}", fa)
    row("dev hard cases: IoU hit / centre hit / 11, unmatched", lambda r: f"{r['dev_hard_cases']['detected_iou']} / {r['dev_hard_cases']['detected_centre']} / {r['dev_hard_cases']['boxes']}, {r['dev_hard_cases']['unmatched']}")

    def boeing(r):
        for i in r['dev_hard_cases']['images']:
            if i['id'].startswith('INS-0005'):
                g = i['gt'][0]
                return f"{g['iou_score']:.3f} vs thr {r['valid']['thresholds']['dent']['score']} → {'detected' if g['detected_iou'] else 'missed'}"
        return "—"
    row("Boeing nose dent INS-0005 (dent score)", boeing)
    row("latency ms/image (container CPU, 2 cores)", lambda r: f"{r['valid']['latency_ms_per_image']:.0f}")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
