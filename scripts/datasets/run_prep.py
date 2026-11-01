"""Run the Phase 4 preparation steps in order (read-only on raw data).

    python scripts/datasets/run_prep.py [--prep VT-PREP-001] [--max-seconds N] [--from STEP]

Steps: inventory, exact, near, annotations, mapping, review, splits, validate, report, report-check.
Exit codes: 0 all steps clean · 1 a step reported findings (discrepancies, invalid annotations, mapping or split
problems) · 2 a step failed with an input/configuration error (later steps are skipped) · 3 the inventory paused
on its time budget (run again; cached work is kept).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ("inventory", "inventory_dataset.py"),
    ("exact", "detect_exact_duplicates.py"),
    ("near", "find_near_duplicates.py"),
    ("annotations", "validate_annotations.py"),
    ("mapping", "validate_class_mapping.py"),
    ("review", "build_review_manifest.py"),
    ("splits", "create_grouped_splits.py"),
    ("validate", "validate_splits.py"),
    ("report", "generate_dataset_quality_report.py"),
    ("report-check", "generate_dataset_quality_report.py --check"),
]


def main() -> int:
    args = sys.argv[1:]
    prep = ["--prep", args[args.index("--prep") + 1]] if "--prep" in args else []
    start = args[args.index("--from") + 1] if "--from" in args else STEPS[0][0]
    names = [s for s, _ in STEPS]
    if start not in names:
        print(f"ERROR: unknown step {start!r}; steps: {names}", file=sys.stderr)
        return 2
    worst = 0
    for name, script in STEPS[names.index(start):]:
        parts = script.split()
        cmd = [sys.executable, str(HERE / parts[0]), *parts[1:], *prep]
        if name == "inventory" and "--max-seconds" in args:
            cmd += ["--max-seconds", args[args.index("--max-seconds") + 1]]
        print(f"== {name}: {' '.join(Path(c).name if i == 1 else c for i, c in enumerate(cmd[1:], 1))}", flush=True)
        code = subprocess.call(cmd)
        if code in (2, 3):
            print(f"== stopped at {name} (exit {code})")
            return code
        worst = max(worst, code)
    print(f"== done (exit {worst})")
    return worst


if __name__ == "__main__":
    sys.exit(main())
