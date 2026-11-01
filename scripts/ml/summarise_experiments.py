"""Print a Markdown summary of every recorded run under experiments/ (from run.json + history.jsonl).

    python scripts/ml/summarise_experiments.py > /tmp/runs.md

Used to fill docs/EXPERIMENT_LOG.md from the records instead of copying numbers by hand.
"""
import json
import sys
from pathlib import Path

import _common  # noqa: F401
from visentra_ml.detection import config as C


def fmt(v, n=4):
    return "—" if v is None else (f"{v:.{n}f}" if isinstance(v, float) else str(v))


def main():
    root = C.ROOT / "experiments"
    for exp in sorted(p for p in root.iterdir() if p.is_dir()):
        print(f"### {exp.name}\n")
        for rd in sorted(p for p in exp.iterdir() if (p / "run.json").is_file()):
            r = json.loads((rd / "run.json").read_text())
            env = json.loads((rd / "environment.json").read_text()) if (rd / "environment.json").is_file() else {}
            print(f"**{r['run_id']}** — status `{r['status']}`{' (smoke)' if r.get('smoke') else ''}; created {r['created_at']}; "
                  f"started {r.get('started_at', '—')}; ended {r.get('ended_at', '—')}; duration {fmt(r.get('duration_seconds'))} s; "
                  f"epochs {r.get('epochs_completed', 0)}/{r.get('epochs_planned', '—')}; config sha256 `{r['config_sha256'][:12]}…`; "
                  f"code {((env.get('source') or {}).get('commit') or '—')[:7]} (ml dirty: {(env.get('source') or {}).get('ml_code_dirty')}); "
                  f"best {json.dumps(r.get('best'))}" + (f"; outcome: {r['outcome']}" if r.get("outcome") else "") + (f"; error: {r['error']}" if r.get("error") else "") + "\n")
            hp = rd / "history.jsonl"
            if hp.is_file():
                print("| epoch | train loss | iou | obj | cls | grad-norm (pre-clip, mean) | valid AP50 | valid AP50–95 | TP/FP/FN @0.25 | train s | valid s |")
                print("|---|---|---|---|---|---|---|---|---|---|---|")
                for line in hp.read_text().splitlines():
                    h = json.loads(line); d = (h.get("valid_per_class") or {}).get("dent", {})
                    print(f"| {h['epoch']} | {h['total_loss']:.3f} | {h['iou_loss']:.3f} | {h['conf_loss']:.3f} | {h['cls_loss']:.3f} | {fmt(h.get('grad_norm_mean_before_clip'), 1)} | "
                          f"{fmt(h.get('valid_AP50'))} | {fmt(h.get('valid_AP50_95'))} | {d.get('tp', '—')}/{d.get('fp', '—')}/{d.get('fn', '—')} | {h['train_seconds']:.0f} | {fmt(h.get('valid_seconds'), 0)} |")
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
