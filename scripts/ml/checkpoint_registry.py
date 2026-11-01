"""Audit every detector/gate checkpoint in the checkout: hash, loadability, configuration, metrics and status.

    python scripts/ml/checkpoint_registry.py --out docs/evidence/phase4/checkpoint_registry.json

Read-only. A checkpoint "loads" when its state dict loads strictly into the architecture its configuration names.
Status comes from the evidence on disk: the bundle the API resolves is ACTIVE; other packaged bundles are SUPERSEDED;
experiment checkpoints are CANDIDATE_NOT_PROMOTED (with the recorded reason) or SMOKE (pipeline checks only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml")); sys.path.insert(0, str(ROOT / "apps" / "api"))
from visentra_ml.detection import artifact as A  # noqa: E402
from visentra_ml.detection import model as Mo  # noqa: E402

NOT_PROMOTED = {
    "VT-EXP-006": "AP50-95 0.0798 (best epoch 24) vs VT-DET-0002 0.0803; tiled variants evaluated, none justified promotion (AI_EVALUATION_REPORT, Phase 2).",
    "VT-EXP-010": "Best epoch 1 of 14 (AP50-95 0.0822). Dent recall 0.1213 vs 0.1549 and crack AP50-95 regress vs VT-DET-0002; the 2026-10-23 re-run is "
                  "bit-identical in best weights and history (Phase 2 / Phase 4).",
}


def strict_load(path: Path, variant: str, n: int) -> tuple[bool, str | None, int | None]:
    try:
        st = torch.load(path, map_location="cpu", weights_only=True)
        sd = st.get("state_dict") or st.get("model") or st
        m = Mo.build(variant, n)
        m.load_state_dict(sd, strict=True)
        return True, None, st.get("epoch") if isinstance(st, dict) else None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:200]}", None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from visentra_api.inspection import analysis as AN
    active_id, active_dir, why = AN.resolve()
    out = {"schema": "visiontra.checkpoint_registry/1", "active_detector": {"model_id": active_id, "bundle": str(active_dir.relative_to(ROOT)) if active_dir else None,
                                                                             "reason_if_none": why},
           "active_gate": AN._resolve_gate()[0], "bundles": [], "gates": [], "experiments": [], "pretrained": []}
    for d in sorted((ROOT / "models/detection").glob("VT-DET-*")):
        meta = json.loads((d / "metadata.json").read_text())
        v = A.verify(d)
        ok, err, ep = strict_load(d / "model.pt", meta["variant"], len(meta["classes"]))
        ev = json.loads((d / "evaluation_valid.json").read_text()) if (d / "evaluation_valid.json").is_file() else {}
        et = json.loads((d / "evaluation_test.json").read_text()) if (d / "evaluation_test.json").is_file() else {}
        out["bundles"].append({"model_id": d.name, "architecture": f"{meta['architecture']}-{meta['variant']}", "input_size": meta["input_size"], "classes": meta["classes"],
                               "readiness": meta["readiness"], "weights_sha256": A.sha256(d / "model.pt"), "checksums_ok": v["ok"], "problems": v.get("problems"),
                               "strict_load": ok, "load_error": err, "experiment": meta.get("experiment"), "thresholds": meta.get("thresholds"),
                               "dataset": {k: (meta.get("dataset") or {}).get(k) for k in ("prep_id", "datasets", "split_digests")},
                               "valid": {k: (ev.get("metrics") or {}).get(k) for k in ("AP50", "AP50_95", "images")},
                               "test": {k: (et.get("metrics") or {}).get(k) for k in ("AP50", "AP50_95", "images")},
                               "status": "ACTIVE" if d.name == active_id else "SUPERSEDED"})
    for d in sorted((ROOT / "models/gate").glob("VT-GATE-*")):
        spec = json.loads((d / "gate.json").read_text())
        sums = dict(reversed(line.split("  ", 1)) for line in (d / "SHA256SUMS").read_text().splitlines())
        out["gates"].append({"gate_id": d.name, "threshold": spec.get("threshold"), "airplane_min_score": spec.get("airplane_min_score"), "uncertain": spec.get("uncertain"),
                             "derived_from": spec.get("derived_from"), "checksums_ok": all(A.sha256(d / n) == h for n, h in sums.items()),
                             "files": {n: A.sha256(d / n) for n in sums}, "status": "ACTIVE" if d.name == out["active_gate"] else "SUPERSEDED"})
    for ck in sorted((ROOT / "experiments").glob("VT-EXP-*/*/checkpoints/*.pt")):
        run = ck.parents[1]
        cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").is_file() else {}
        rec = json.loads((run / "run.json").read_text()) if (run / "run.json").is_file() else {}
        variant = (cfg.get("model") or {}).get("variant", "tiny"); n = len(rec.get("classes") or ["crack", "dent", "corrosion"])
        if ck.name == "last.pt":
            ok, err, ep = True, None, None             # resume state (model + optimiser); its weights are not served
            try:
                st = torch.load(ck, map_location="cpu", weights_only=True); Mo.build(variant, n).load_state_dict(st["model"], strict=True); ep = st.get("epoch")
            except Exception as e:  # noqa: BLE001
                ok, err = False, f"{type(e).__name__}: {str(e)[:200]}"
        else:
            ok, err, ep = strict_load(ck, variant, n)
        exp = cfg.get("experiment_id") or run.parent.name
        smoke = run.name.endswith("-smoke")
        out["experiments"].append({"experiment_id": exp, "run_id": run.name, "file": str(ck.relative_to(ROOT)), "kind": ck.stem, "sha256": A.sha256(ck),
                                   "bytes": ck.stat().st_size, "strict_load": ok, "load_error": err, "epoch": ep, "input_size": (cfg.get("model") or {}).get("input_size"),
                                   "config_sha256": rec.get("config_sha256"), "run_status": rec.get("status"), "best": rec.get("best"),
                                   "epochs_completed": rec.get("epochs_completed"), "pretrained_from": (rec.get("pretrained") or {}).get("source_url"),
                                   "status": "SMOKE" if smoke else "CANDIDATE_NOT_PROMOTED", "reason": None if smoke else NOT_PROMOTED.get(exp, "not evaluated for promotion")})
    for p in sorted((ROOT / "models/pretrained").glob("*")):
        out["pretrained"].append({"file": str(p.relative_to(ROOT)), "sha256": A.sha256(p), "bytes": p.stat().st_size})
    Path(ROOT / a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"active": out["active_detector"], "gate": out["active_gate"], "bundles": [(b["model_id"], b["status"], b["strict_load"], b["checksums_ok"]) for b in out["bundles"]],
                      "experiments": [(e["run_id"], e["kind"], e["strict_load"], e["sha256"][:12]) for e in out["experiments"]]}, indent=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
