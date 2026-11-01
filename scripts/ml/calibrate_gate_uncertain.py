"""Calibrate the domain gate's three-state policy (relevant · uncertain · irrelevant) and package a new gate bundle.

    python scripts/ml/calibrate_gate_uncertain.py --from VT-GATE-0001 --gate-id VT-GATE-0002 [--max-other-admitted 0.08]

Why: VT-GATE-0001 hard-rejected a genuine close-up of a hail-damaged A319 nose (p = 0.0003, "looks like: donut").
Close-ups that fill the frame do not look like an aircraft to a whole-image classifier. The recovery policy:

* Five overlapping crops are scored as well as the full image (domain_gate.Gate.views).
* relevant   — full image passes the VT-GATE-0001 rule, or (if it keeps specificity on validation) a crop passes it.
* uncertain  — weaker aircraft evidence (p or airplane score above a lower band, full image or any crop). The image IS
               analysed; the result is flagged so the engineer confirms relevance. Nothing is silently discarded.
* irrelevant — none of the above; detection is skipped (the engineer can still override with a reason).

The band is chosen on the gate's VALIDATION split only: maximise aircraft images that are not hard-rejected, subject to
at most --max-other-admitted of the non-aircraft validation images being admitted (relevant or uncertain). The test
split is scored once, after the choice, and reported. The classifier weights and COCO features are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "scripts" / "ml"))
from visentra_ml.detection import domain_gate as G  # noqa: E402
import train_domain_gate as T  # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def evidence_rows(g: G.Gate, files: list[str]) -> list[dict]:
    out = []
    for i, f in enumerate(files):
        with Image.open(ROOT / f) as im:
            im.load()
            out.append(g.evidence(im))
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(files)}", flush=True)
    return out


def apply(g: G.Gate, rows, spec_u):
    g.spec["uncertain"] = spec_u
    return [g.decide(r)[0] for r in rows]


def summary(states, y) -> dict:
    y = np.asarray(y); s = np.asarray(states)
    air, oth = y == 1, y == 0
    f = lambda m, k: int(((s == k) & m).sum())  # noqa: E731
    return {"aircraft": {"n": int(air.sum()), "relevant": f(air, "relevant"), "uncertain": f(air, "uncertain"), "irrelevant": f(air, "irrelevant"),
                         "not_rejected_rate": round(float(((s != "irrelevant") & air).sum() / max(1, air.sum())), 4)},
            "other": {"n": int(oth.sum()), "relevant": f(oth, "relevant"), "uncertain": f(oth, "uncertain"), "irrelevant": f(oth, "irrelevant"),
                      "admitted_rate": round(float(((s != "irrelevant") & oth).sum() / max(1, oth.sum())), 4)}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", default="VT-GATE-0001")
    ap.add_argument("--gate-id", default="VT-GATE-0002")
    ap.add_argument("--max-other-admitted", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=20261017)
    ap.add_argument("--cache", help="JSON file to cache per-image evidence (re-runs skip feature extraction)")
    ap.add_argument("--probe", nargs="*", default=[], help="incident images to report (never used to choose the band)")
    a = ap.parse_args()
    src = ROOT / "models" / "gate" / a.src
    g = G.Gate(src)
    rng = random.Random(a.seed)
    t0 = time.time()
    data = {}
    cache = Path(a.cache) if a.cache else None
    if cache and cache.exists():
        data = {k: tuple(v) for k, v in json.loads(cache.read_text()).items()}
    for split in ("valid", "test"):
        if split in data:
            continue
        pos = T.positives(split, None, rng)
        neg, pos_extra = T.negatives(split)
        files = [p for p, _ in pos + pos_extra] + [p for p, _ in neg]
        y = [1] * (len(pos) + len(pos_extra)) + [0] * len(neg)
        print(f"{split}: {sum(y)} aircraft, {len(y) - sum(y)} other — scoring 6 views each", flush=True)
        data[split] = (evidence_rows(g, files), y, files)
        if cache:
            cache.write_text(json.dumps(data))

    rv, yv, _ = data["valid"]
    base = summary(apply(g, rv, None), yv)
    # 1) does "a crop passes the v1 rule" keep specificity?
    crop_only = {"p_min": 1.01, "airplane_min": 1.01}
    with_crop = summary(apply(g, rv, crop_only), yv)
    # 2) grid over the uncertain band
    best = None
    for pm in [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]:
        for am in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 1.01]:
            u = {"p_min": pm, "airplane_min": am}
            sm = summary(apply(g, rv, u), yv)
            if sm["other"]["admitted_rate"] > a.max_other_admitted:
                continue
            # Validation aircraft images are in-distribution: nearly all pass already, so recall cannot separate the
            # bands. The band therefore spends the specificity budget: the most permissive band (lowest thresholds)
            # whose non-aircraft admission stays within --max-other-admitted. Hard close-ups (the incident images) are
            # reported as probes only, never used to choose.
            key = (sm["aircraft"]["not_rejected_rate"], -pm, -am)
            if best is None or key > best[0]:
                best = (key, u, sm)
    if best is None:
        print("No band meets the specificity limit; nothing packaged."); return 2
    u = {**best[1], "views": "full + 5 crops (60 %)", "rule": f"validation only: maximise aircraft not-rejected rate with non-aircraft admitted ≤ {a.max_other_admitted}"}
    states_t = apply(g, data["test"][0], u)
    test = summary(states_t, data["test"][1])
    test_base = summary(apply(g, data["test"][0], None), data["test"][1])
    errors = [{"file": f, "label": "aircraft" if yy else "other", "state": s} for f, yy, s in zip(data["test"][2], data["test"][1], states_t)
              if (yy == 1 and s == "irrelevant") or (yy == 0 and s != "irrelevant")]

    probes = []
    for f in a.probe:
        with Image.open(f) as im:
            im.load(); e = g.evidence(im)
        probes.append({"file": Path(f).name, "evidence": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in e.items()},
                       "v1": apply(g, [e], None)[0], "three_state": apply(g, [e], u)[0]})
    out = ROOT / "models" / "gate" / a.gate_id
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "coco.pth", out / "coco.pth")
    spec = json.loads((src / "gate.json").read_text())
    spec.update({"gate_id": a.gate_id, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "derived_from": a.src, "uncertain": u})
    (out / "gate.json").write_text(json.dumps(spec, indent=1))
    ev = {"valid": {"v1_rule": base, "v1_rule_plus_crops": with_crop, "three_state": best[2]},
          "test": {"v1_rule": test_base, "three_state": test}, "test_errors": errors, "incident_probes": probes, "seconds": round(time.time() - t0, 1)}
    (out / "evaluation.json").write_text(json.dumps(ev, indent=1))
    (out / "SHA256SUMS").write_text("".join(f"{sha(out / n)}  {n}\n" for n in ("coco.pth", "gate.json")))
    print(json.dumps({"uncertain": u, "valid": ev["valid"], "test": ev["test"], "test_errors": len(errors), "incident_probes": probes}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
