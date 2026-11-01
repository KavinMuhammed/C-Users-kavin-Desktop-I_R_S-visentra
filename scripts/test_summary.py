"""Machine-readable summary of one or more JUnit XML files.

    python scripts/test_summary.py docs/evidence/phase4/full_suite_final.xml [...] --out artifacts/recovery/test_summary.json
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as E
from datetime import datetime, timezone
from pathlib import Path


def summarise(p: Path) -> dict:
    r = E.parse(p).getroot()
    suites = [r] if r.tag == "testsuite" else list(r)
    tot = {k: sum(int(s.get(k) or 0) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    if not tot["tests"]:                                  # node --test's JUnit reporter puts no counts on the suite elements
        cases = list(r.iter("testcase"))
        tot = {"tests": len(cases), **{k: sum(1 for tc in cases for ch in tc if ch.tag == t) for k, t in
                                       (("failures", "failure"), ("errors", "error"), ("skipped", "skipped"))}}
    tot["passed"] = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
    issues = [{"kind": ch.tag, "test": f"{tc.get('classname')}::{tc.get('name')}", "message": (ch.get("message") or "")[:300]}
              for s in suites for tc in s.iter("testcase") for ch in tc if ch.tag in ("failure", "error", "skipped")]
    return {"file": p.as_posix(), "timestamp": suites[0].get("timestamp"), "seconds": sum(float(s.get("time") or 0) for s in suites), **tot, "issues": issues}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": [summarise(Path(f)) for f in a.files]}
    txt = json.dumps(out, indent=1)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
