#!/usr/bin/env python3
"""Check that a transferred VT-DET-#### bundle and its records are present and consistent.

Standard library only, so it runs where PyTorch is unavailable. It reads records, documents and
split manifests/labels; it never loads images for inference, never evaluates the model and never
writes anything. The held-out test split is only re-*digested* (file ids), not evaluated.

    python3 scripts/ml/check_transfer_consistency.py models/detection/VT-DET-0001 [--git-dir DIR]
Exit 0 = all checks pass, 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--git-dir", help="clone of the history bundle, to check the source revisions")
    ap.add_argument("--expect-head", default="4322a17")
    a = ap.parse_args()
    b = (ROOT / a.bundle).resolve() if not Path(a.bundle).is_absolute() else Path(a.bundle)

    # 1. presence + checksums
    need = ["metadata.json", "SHA256SUMS", "config.json", "environment.json", "run.json", "thresholds.json",
            "evaluation_valid.json", "evaluation_test.json", "model.pt"]
    missing = [n for n in need if not (b / n).is_file()]
    check("bundle files present", not missing, f"missing: {missing}" if missing else f"{len(need)} files")
    sums = {}
    for line in (b / "SHA256SUMS").read_text().splitlines():
        if line.strip():
            h, n = line.split(maxsplit=1)
            sums[n.lstrip("*")] = h
    bad = [n for n, h in sums.items() if not (b / n).is_file() or sha(b / n) != h]
    check("SHA256SUMS verify", not bad, f"mismatch: {bad}" if bad else f"{len(sums)} files OK")
    m = json.loads((b / "metadata.json").read_text())
    badm = [n for n, v in m["files"].items() if sums.get(n) != v["sha256"] or (b / n).stat().st_size != v["bytes"]]
    check("metadata file list == SHA256SUMS == sizes", not badm, str(badm) if badm else "")

    # 2. identity, scope, readiness
    check("model_id", m["model_id"] == b.name, m["model_id"])
    check("readiness EVALUATED_LIMITED", m["readiness"] == "EVALUATED_LIMITED", m["readiness"])
    check("classes == ['dent']", m["classes"] == ["dent"], str(m["classes"]))
    check("supported_classes empty", m["supported_classes"] == [], str(m["supported_classes"]))
    check("experimental_classes == ['dent']", m["experimental_classes"] == ["dent"], str(m["experimental_classes"]))
    th = m["thresholds"]
    check("thresholds score 0.05 / review 0.075", th["score_threshold"] == {"dent": 0.05} and th["review_threshold"] == {"dent": 0.075},
          f"{th['score_threshold']} {th['review_threshold']}")
    tj = json.loads((b / "thresholds.json").read_text())
    check("thresholds.json agrees", json.dumps(tj).count("0.075") > 0 and json.dumps(tj).count("0.05") > 0)

    # 3. experiment record and configuration
    exp = m["experiment"]
    run = json.loads((b / "run.json").read_text())
    check("run.json completed, same run id", run["status"] == "completed" and run["run_id"] == exp["run_id"], f"{run['status']} {run['run_id']}")
    cfg_repo = ROOT / "ml/configs/detection" / f"{exp['experiment_id']}.json"
    check("config in repo == bundle config.json", cfg_repo.is_file() and sha(cfg_repo) == sha(b / "config.json"), str(cfg_repo.relative_to(ROOT)))
    check("run config_sha256 == metadata", run["config_sha256"] == exp["config_sha256"])
    rundir = ROOT / "experiments" / exp["experiment_id"] / exp["run_id"]
    rec = [n for n in ("run.json", "config.json", "environment.json", "history.jsonl", "train.log") if not (rundir / n).is_file()]
    check("experiment run records present", not rec, f"missing {rec}" if rec else str(rundir.relative_to(ROOT)))
    if (rundir / "run.json").is_file():
        check("experiment run.json == bundle run.json", sha(rundir / "run.json") == sha(b / "run.json"))
    for e in ("VT-EXP-001", "VT-EXP-002"):
        check(f"{e} records present", any((ROOT / "experiments" / e).glob("readiness-*.json")))
    check("VT-EXP-001 failure diagnosis present", (ROOT / "experiments/VT-EXP-001/STABILITY_PROBES.md").is_file())
    check("VT-EXP-002 blocked config present", (ROOT / "ml/configs/detection/VT-EXP-002-blocked.json").is_file())

    # 4. evaluation records == metadata == documents
    et = json.loads((b / "evaluation_test.json").read_text())
    ev = json.loads((b / "evaluation_valid.json").read_text())
    mt = m["evaluation"]["test"]
    check("test record: split test, one evaluation", et["split"] == "test" and et["evaluated_at"] == mt["evaluated_at"], et["evaluated_at"])
    check("test metrics == metadata", et["metrics"]["AP50"] == mt["AP50"] and et["metrics"]["AP50_95"] == mt["AP50_95"]
          and et["metrics"]["per_class_at_threshold"] == mt["per_class_at_threshold"],
          f"AP50 {mt['AP50']:.3f} AP50-95 {mt['AP50_95']:.3f}")
    check("test split digest == metadata", et["split_digest"] == m["dataset"]["test_split_digest"] == mt["split_digest"], et["split_digest"][:12])
    check("valid split digest == metadata", ev["split_digest"] == m["dataset"]["valid_split_digest"], ev["split_digest"][:12])
    d = mt["per_class_at_threshold"]["dent"]
    want = {"MODEL_CARD.md": [f"{mt['AP50']:.3f}", f"{mt['AP50_95']:.3f}", f"{d['precision']:.2f} / {d['recall']:.2f}", "EVALUATED_LIMITED"],
            "MODEL_EVALUATION_REPORT.md": [f"{mt['AP50']:.3f}", f"{mt['AP50_95']:.3f}"],
            "MODEL_ARTIFACTS.md": [m["files"]["model.pt"]["sha256"]],
            "PHASE_7_COMPLETION.md": [f"{mt['AP50']:.3f}", "EVALUATED_LIMITED"]}
    for doc, needles in want.items():
        p = ROOT / "docs" / doc
        txt = p.read_text(encoding="utf-8") if p.is_file() else ""
        miss = [n for n in needles if n not in txt]
        check(f"docs/{doc} present and quotes the recorded values", p.is_file() and not miss, f"missing {miss}" if miss else "")

    # 5. the test split on disk still has the recorded digest (file ids only; no evaluation)
    try:
        from visentra_ml.detection import config as C, data as D
        cfg = json.loads(cfg_repo.read_text())
        mp = D.load_mapping(cfg)
        for split, key, rec_ in (("test", "test_subset", et), ("valid", "valid_subset", ev)):
            items, _ = D.load_items(cfg, split, mp, subset=cfg["dataset"][key])
            check(f"{split} split on disk has the recorded digest", D.split_digest(items) == rec_["split_digest"], f"{len(items)} items")
    except Exception as e:  # noqa: BLE001 - report, do not hide
        check("split digests recomputed", False, f"{type(e).__name__}: {e}")

    # 6. pretrained initialisation
    pre = ROOT / "models/pretrained/yolox_tiny_coco_0.1.1rc0.pth"
    check("pretrained file sha == metadata", pre.is_file() and sha(pre) == m["pretrained"]["sha256"], m["pretrained"]["sha256"][:12])

    # 7. source revisions
    env = json.loads((b / "environment.json").read_text())
    commit = env["source"]["commit"]
    check("training commit recorded consistently", commit == exp["source"]["commit"] and not env["source"]["ml_code_dirty"], commit[:7])
    if a.git_dir:
        g = lambda *x: subprocess.run(["git", "-C", a.git_dir, *x], capture_output=True, text=True)
        head = g("rev-parse", "HEAD").stdout.strip()
        check("history HEAD", head.startswith(a.expect_head), head[:7])
        check("training commit is an ancestor of HEAD", g("merge-base", "--is-ancestor", commit, "HEAD").returncode == 0, commit[:7])
        dif = g("diff", "--stat", commit, "HEAD", "--", "ml/visentra_ml/detection/model.py", "ml/visentra_ml/detection/third_party").stdout.strip()
        check("model definition unchanged since training", dif == "", dif or "no diff")
        tracked = g("ls-files", "ml/visentra_ml/detection", "scripts/ml", "ml/configs/detection").stdout.split()
        diff_files = [f for f in tracked if not (ROOT / f).is_file() or sha(ROOT / f) != hashlib.sha256(
            subprocess.run(["git", "-C", a.git_dir, "show", f"HEAD:{f}"], capture_output=True).stdout).hexdigest()]
        check("working-tree ML code == HEAD", not diff_files, f"{len(tracked)} files" if not diff_files else str(diff_files))

    w = max(len(n) for _, n, _ in results)
    for s, n, dtl in results:
        print(f"{s}  {n.ljust(w)}  {dtl}")
    nf = sum(s == "FAIL" for s, _, _ in results)
    print(f"\n{len(results) - nf} passed, {nf} failed")
    return 1 if nf else 0


if __name__ == "__main__":
    sys.exit(main())
