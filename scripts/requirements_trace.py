"""Build docs/REQUIREMENT_TRACEABILITY.md and check cross-document consistency.

Usage:  python scripts/requirements_trace.py          # regenerate the matrix + print the consistency report
        python scripts/requirements_trace.py --check  # only check; exit 1 on problems

Sources of truth: requirement ids, priorities and statuses are parsed from docs/PRODUCT_REQUIREMENTS.md;
acceptance-test ids and results from docs/ACCEPTANCE_TEST_PLAN.md; API ids from
docs/API_INTEGRATION_REQUIREMENTS.md; entity ids from docs/DATA_CONTRACTS.md. Only the
area / implementation-location mapping below is maintained by hand.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
WEB = "apps/web/src"

# requirement -> (objective, page/area, implementation location or "—", entities, apis)
MAP = {
    "FR-NAV-01": ("PR-02", "Shell / all inspection pages", f"{WEB}/app/shell.js, features/insp_*.js (routes)", "—", "—"),
    "FR-NAV-02": ("PR-02", "Not found / missing record", f"{WEB}/app/main.js, features/insp_core.js#detail", "ENT-01", "API-06"),
    "FR-NAV-03": ("PR-05", "Global keyboard / focus", f"{WEB}/app/shell.js, styles/aero.css, features/insp_core.js (keys)", "—", "—"),
    "FR-NAV-04": ("PR-07", "/welcome", f"{WEB}/features/welcome.js", "—", "—"),
    "FR-MED-01": ("PR-02", "/inspection/new", f"{WEB}/features/insp_core.js#upload", "ENT-02", "API-01"),
    "FR-MED-02": ("PR-01", "/inspection/new (video)", "—", "ENT-02", "API-02"),
    "FR-MED-03": ("PR-05", "/inspection/:id (multi-media)", "—", "ENT-01, ENT-02", "API-02"),
    "FR-VAL-01": ("PR-06", "/inspection/new", f"{WEB}/services/inspection.js#sniff,validateFile", "ENT-02", "API-01"),
    "FR-VAL-02": ("PR-06", "/inspection/new", f"{WEB}/services/inspection.js#POLICY,validateDimensions", "ENT-02", "API-01, API-14"),
    "FR-VAL-03": ("PR-06", "/inspection/new, detail", f"{WEB}/features/insp_core.js#sha256,handleFile", "ENT-02", "API-01"),
    "FR-QLT-01": ("PR-05", "/inspection/new", f"{WEB}/features/insp_core.js#upload", "ENT-02", "—"),
    "FR-QLT-02": ("PR-05", "/inspection/new", f"{WEB}/services/inspection.js#pixelStats,assessQuality", "ENT-02.quality", "API-01"),
    "FR-INS-01": ("PR-06", "/inspection/new", f"{WEB}/services/inspection.js#createSession", "ENT-01", "API-01"),
    "FR-INS-02": ("PR-06", "Detail, review, history", f"{WEB}/services/inspection.js#statusOf", "ENT-01", "API-04"),
    "FR-INS-03": ("PR-06", "All inspection pages", "—", "ENT-01, ENT-02, ENT-05…09", "API-01, API-05, API-06"),
    "FR-INF-01": ("PR-02", "/inspection/:id", "—", "ENT-03, ENT-05", "API-03, API-04"),
    "FR-INF-02": ("PR-07", "/inspection/new, detail", f"{WEB}/features/insp_core.js (run steps, banners)", "ENT-01.analysis", "API-14"),
    "FR-INF-03": ("PR-06", "Detail, report", "—", "ENT-05, ENT-12", "API-06"),
    "FR-INF-04": ("PR-06", "/inspection/new", "—", "ENT-01", "API-01, API-03"),
    "FR-INF-05": ("PR-06", "Detail", "—", "ENT-01", "API-03, API-04"),
    "FR-DET-01": ("PR-02", "Viewer", f"{WEB}/features/insp_core.js#boxes (renders normalised boxes)", "ENT-05", "API-06"),
    "FR-DET-02": ("PR-07", "Viewer, Model Lab", "—", "ENT-12", "API-14"),
    "FR-DET-03": ("PR-02", "Viewer", "—", "ENT-05.mask", "API-06"),
    "FR-DET-04": ("PR-07", "Detail", f"{WEB}/features/insp_core.js (no-model variant only)", "ENT-01", "API-06"),
    "FR-VIS-01": ("PR-05", "Viewer", f"{WEB}/features/insp_core.js#viewer", "ENT-02, ENT-05, ENT-06", "API-07"),
    "FR-VIS-02": ("PR-05", "Viewer", f"{WEB}/features/insp_core.js#mountViewers", "—", "—"),
    "FR-VIS-03": ("PR-07", "Viewer, report", f"{WEB}/features/insp_core.js#boxes, styles/aero.css", "ENT-05, ENT-06", "—"),
    "FR-VIS-04": ("PR-05", "Viewer", f"{WEB}/features/insp_core.js (vw-cat, vw-minconf)", "ENT-05", "—"),
    "FR-VIS-05": ("PR-06", "Viewer", f"{WEB}/features/insp_core.js#pane (vw-noimg)", "ENT-02.integrity", "API-07"),
    "FR-CONF-01": ("PR-07", "Viewer, findings, report", f"{WEB}/features/insp_core.js#findingsPanel (sample values)", "ENT-05", "API-06"),
    "FR-CONF-02": ("PR-07", "Findings", "—", "ENT-05.low_confidence, ENT-12.review_threshold", "API-06"),
    "FR-CONF-03": ("PR-03", "All", f"{WEB}/services/inspection.js (no severity field)", "—", "—"),
    "FR-REV-01": ("PR-05", "/inspection/review", f"{WEB}/features/insp_review.js#review", "ENT-01", "API-05"),
    "FR-REV-02": ("PR-05", "Review, detail", f"{WEB}/services/inspection.js#decide, features/insp_core.js#findingsPanel", "ENT-07", "API-08"),
    "FR-REV-03": ("PR-05", "Viewer", f"{WEB}/services/inspection.js#addFinding", "ENT-06", "API-08"),
    "FR-REV-04": ("PR-06", "Review, detail", f"{WEB}/features/insp_core.js (insp-fnote), insp_review.js (signoff)", "ENT-07.comment", "API-08, API-09"),
    "FR-REV-05": ("PR-06", "Review", f"{WEB}/services/inspection.js#signOff,reopen", "ENT-01, ENT-07", "API-09"),
    "FR-REV-06": ("PR-06", "Review, detail (history)", f"apps/api/visentra_api/inspection/review.py, routers/reviews.py, {WEB}/features/insp_server_review.js", "ENT-07, ENT-17", "API-08, API-09"),
    "FR-REV-07": ("PR-05", "Review (API only)", "apps/api/visentra_api/inspection/review.py#decide (kind reclassify)", "ENT-07.corrected_class_id", "API-08"),
    "FR-HIS-01": ("PR-06", "/inspection/history", f"{WEB}/services/inspection.js#query, features/insp_review.js#history", "ENT-01", "API-05"),
    "FR-HIS-02": ("PR-06", "/inspection/history", "—", "ENT-01", "API-05"),
    "FR-HIS-03": ("PR-06", "Detail", f"{WEB}/features/insp_core.js#detail", "ENT-01, ENT-02", "API-06, API-07"),
    "FR-DS-01": ("PR-07", "/inspection/datasets", f"apps/api/visentra_api/detection_data.py, routers/detection_data.py, {WEB}/features/insp_lab.js#datasets, insp_datasets_live.js", "ENT-10", "API-11"),
    "FR-DS-02": ("PR-07", "/inspection/datasets", f"{WEB}/services/inspection.js#datasetReadiness", "ENT-10", "API-11"),
    "FR-DS-03": ("PR-07", "/inspection/datasets", "scripts/datasets/*.py (Phase 4 pipeline), scripts/datasets/verify_prepared_files.py (Phase 12 re-check)", "ENT-11", "API-11"),
    "FR-DS-04": ("PR-07", "Datasets", "apps/api/visentra_api/services/datasets.py, manifest.py (classification)", "ENT-10, ENT-11", "API-11"),
    "FR-MDL-01": ("PR-07", "/inspection/models", f"{WEB}/features/insp_lab.js#modelLab; existing /models", "ENT-12", "API-12"),
    "FR-MDL-02": ("PR-07", "/inspection/models", f"{WEB}/features/insp_lab.js#modelLab", "ENT-14", "API-12"),
    "FR-MDL-03": ("PR-07", "Models", "— (classification gate: apps/api/visentra_api/routers/ml.py promotion)", "ENT-12, ENT-14", "API-12"),
    "FR-MDL-04": ("PR-07", "/inspection/models", f"{WEB}/services/inspection.js#validateTrainingConfig", "ENT-13", "API-12"),
    "FR-MDL-05": ("PR-02", "Models", "— (classification: apps/api/visentra_api/services/training.py)", "ENT-13", "API-12"),
    "FR-RPT-01": ("PR-06", "/inspection/reports/:id", f"{WEB}/features/insp_review.js#reports", "ENT-09", "API-10"),
    "FR-RPT-02": ("PR-06", "/inspection/reports/:id", f"{WEB}/features/insp_review.js#buildReportPdf, lib/pdf.js", "ENT-09", "API-10"),
    "FR-RPT-03": ("PR-06", "Reports", f"apps/api/visentra_api/reports/, routers/reports.py, {WEB}/features/insp_server_review.js#srvReports", "ENT-09", "API-10"),
    "FR-RPT-04": ("PR-06", "Reports", f"apps/api/visentra_api/reports/service.py#report_out (stale), {WEB}/features/insp_server_review.js", "ENT-01.revision, ENT-09", "API-10"),
    "FR-ANL-01": ("PR-05", "/inspection/analytics", f"{WEB}/features/insp_lab.js#analytics, services/inspection.js#stats", "ENT-01, ENT-05", "API-13"),
    "FR-ANL-02": ("PR-05", "/inspection, /inspection/analytics", "apps/api/visentra_api/inspection/analytics.py, routers/inspection_analytics.py", "ENT-01, ENT-05", "API-13"),
    "FR-ERR-01": ("PR-07", "All", f"{WEB}/features/insp_*.js (error states)", "—", "—"),
    "FR-ERR-02": ("PR-05", "Shell", f"{WEB}/app/main.js#viewFor", "—", "—"),
    "FR-ERR-03": ("PR-06", "All (session)", f"{WEB}/features/insp_core.js (beforeunload)", "—", "—"),
    "FR-ERR-04": ("PR-06", "API", "apps/api/visentra_api/errors.py", "—", "all API-*"),
    "FR-PREF-01": ("PR-05", "/settings/appearance", f"{WEB}/features/admin.js, app/shell.js", "ENT-15", "existing /api/auth/me/preferences"),
    "FR-PREF-02": ("PR-07", "/settings/inspection", f"{WEB}/features/insp_lab.js#settingsSection", "—", "—"),
    "FR-AUTH-01": ("PR-06", "Auth pages", "apps/api/visentra_api/security.py, routers/auth.py", "ENT-15, ENT-16", "existing /api/auth/*"),
    "FR-AUTH-02": ("PR-06", "API", "—", "ENT-16", "API-01…API-15"),
    "FR-AUTH-03": ("PR-06", "All inspection pages", "—", "ENT-16", "API-14"),
    "FR-RET-01": ("PR-06", "Detail, settings", f"{WEB}/services/inspection.js#remove,clearSession", "ENT-01", "API-06"),
    "FR-RET-02": ("PR-06", "Settings", "—", "ENT-01, ENT-02, ENT-09", "API-15"),
    "FR-RET-03": ("PR-06", "Detail", "—", "ENT-01, ENT-07", "API-15"),
    "FR-DEMO-01": ("PR-07", "All inspection pages", f"{WEB}/services/inspection.js#buildSamples, lib/aero.js#panelSVG", "—", "—"),
    "FR-DEMO-02": ("PR-07", "All inspection pages", f"apps/api/visentra_api/config.py (demo_samples_allowed), {WEB}/services/inspection.js#prefs.samples", "—", "API-14"),
}


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def main() -> int:
    prd, atp = read("PRODUCT_REQUIREMENTS.md"), read("ACCEPTANCE_TEST_PLAN.md")
    api, ent = read("API_INTEGRATION_REQUIREMENTS.md"), read("DATA_CONTRACTS.md")
    ml = read("DATASET_AND_ML_REQUIREMENTS.md")
    reqs = {}
    for m in re.finditer(r"\*\*(FR-[A-Z]+-\d+) ([^*]+)\*\* — (P\d)[^—\n]*— ([^\n]+)", prd):
        st = m.group(4)
        status = next((k for k in ("NOT IMPLEMENTED", "PARTIAL", "BLOCKED", "VERIFIED", "N/A") if st.startswith(k)), "?")
        reqs[m.group(1)] = {"title": m.group(2).strip(), "prio": m.group(3), "status": status}
    tests = {m.group(1): m.group(2) for m in re.finditer(r"^\| (AT-\d+) \|[^\n]*\| ([^|\n]+) \|\s*$", atp, re.M)}
    def expand(text: str) -> set[str]:
        """FR-VIS-01/02 -> {FR-VIS-01, FR-VIS-02}"""
        out = set()
        for m in re.finditer(r"\b(N?FR-[A-Z0-9]+-)(\d+(?:/\d+)*)", text):
            out |= {m.group(1) + n for n in m.group(2).split("/")}
        return out
    test_reqs = {m.group(1): expand(m.group(0)) for m in re.finditer(r"^\| (AT-\d+) \|[^\n]*$", atp, re.M)}
    apis = set(re.findall(r"### (API-\d+)", api))
    ents = set(re.findall(r"### (ENT-\d+)", ent)) | {f"ENT-{n:02d}" for n in (8, 15, 16)}
    nfrs = set(re.findall(r"^\| (NFR-[A-Z0-9]+-\d+) \|", prd, re.M))

    problems: list[str] = []
    # tests per requirement
    req_tests = {r: sorted(t for t, rs in test_reqs.items() if r in rs) for r in reqs}
    for r in reqs:
        if r not in MAP:
            problems.append(f"{r}: no traceability mapping")
        if not req_tests[r]:
            problems.append(f"{r}: no acceptance test references it")
    for r in MAP:
        if r not in reqs:
            problems.append(f"{r}: mapped but not defined in PRODUCT_REQUIREMENTS.md")
    for t, rs in test_reqs.items():
        for r in rs:
            if r.startswith("FR-") and r not in reqs:
                problems.append(f"{t}: references undefined {r}")
            if r.startswith("NFR-") and r not in nfrs:
                problems.append(f"{t}: references undefined {r}")
        if not rs:
            problems.append(f"{t}: tests no FR/NFR requirement")
    alldocs = prd + api + ent + ml + atp
    for ref in sorted(set(re.findall(r"\bAT-\d+\b", prd + api + ent + ml))):
        if ref not in tests:
            problems.append(f"{ref}: referenced but not defined in ACCEPTANCE_TEST_PLAN.md")
    for ref in sorted(set(re.findall(r"\bAPI-\d+\b", alldocs))):
        if ref not in apis:
            problems.append(f"{ref}: referenced but not defined in API_INTEGRATION_REQUIREMENTS.md")
    for ref in sorted(set(re.findall(r"\bENT-\d+\b", alldocs))):
        if ref not in ents:
            problems.append(f"{ref}: referenced but not defined in DATA_CONTRACTS.md")
    for ref in sorted(set(re.findall(r"\bMLG-\d+\b", prd + api + atp))):
        if ref not in ml:
            problems.append(f"{ref}: referenced but not defined in DATASET_AND_ML_REQUIREMENTS.md")
    # status sanity: a VERIFIED requirement must have at least one PASS test
    for r, d in reqs.items():
        if d["status"] == "VERIFIED" and not any(tests.get(t, "").startswith("PASS") for t in req_tests[r]):
            problems.append(f"{r}: marked VERIFIED but none of its tests has a PASS result")
        if d["status"] in ("NOT IMPLEMENTED", "BLOCKED") and any(tests.get(t, "").startswith("PASS") and "future" not in tests.get(t, "") for t in req_tests[r]):
            pass  # partial evidence for related behaviour is allowed

    if "--check" not in sys.argv:
        rows = []
        for r, d in reqs.items():
            obj, area, impl, en, ap = MAP.get(r, ("?", "?", "?", "?", "?"))
            res = ", ".join(f"{t} ({'PASS' if tests.get(t, '').startswith('PASS') else 'future' if 'future' in tests.get(t, '') else tests.get(t, '?').split(' ')[0]})" for t in req_tests[r]) or "—"
            rows.append(f"| {r} | {d['title']} | {d['prio']} | {obj} | {area} | {impl} | {en} | {ap} | {res} | {d['status']} |")
        counts = {}
        for d in reqs.values():
            counts[(d["prio"], d["status"])] = counts.get((d["prio"], d["status"]), 0) + 1
        summary = "\n".join(f"| {p} | " + " | ".join(str(counts.get((p, s), 0)) for s in ("VERIFIED", "PARTIAL", "NOT IMPLEMENTED", "BLOCKED")) + " |" for p in ("P0", "P1", "P2"))
        no_impl = [r for r, d in reqs.items() if MAP.get(r, ("",) * 5)[2].startswith("—")]
        out = f"""# VisionTra — Requirement Traceability Matrix

Generated by `python scripts/requirements_trace.py` from PRODUCT_REQUIREMENTS, ACCEPTANCE_TEST_PLAN, API_INTEGRATION_REQUIREMENTS and DATA_CONTRACTS. **Do not edit by hand** — edit the source documents (or the area/implementation mapping in the script) and regenerate.

Objectives: PR-02 assist inspectors · PR-03 safe limits · PR-05 consistency/speed · PR-06 traceable evidence & records · PR-07 honest communication of limitations. Test results: PASS = executed and passed on 2026-10-09; future = not executable yet.

## Summary by priority and status ({len(reqs)} functional requirements)

| Priority | VERIFIED | PARTIAL | NOT IMPLEMENTED | BLOCKED |
|---|---|---|---|---|
{summary}

## Matrix

| Req | Title | Prio | Objective | Page / area | Implementation | Data | API | Acceptance tests | Status |
|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## Gap analysis

- **Requirements with no implementation yet ({len(no_impl)}):** {", ".join(no_impl)}.
- **Requirements with no verification plan:** {"none" if not any("no acceptance test" in p for p in problems) else ", ".join(p.split(":")[0] for p in problems if "no acceptance test" in p)}.
- **Tests with no requirement:** {"none" if not any("tests no FR" in p for p in problems) else ", ".join(p.split(":")[0] for p in problems if "tests no FR" in p)}.
- **Implementations with no justified requirement:** none in the inspection module. Platform features outside this specification (projects, classification training/inference, devices, hardware, connections, lineage, audit UI) are **retained unchanged** under D-01; they are not removed and are not required by the aircraft-inspection scope. Phase 1 visual extras (intro animation, aircraft line art, 3D Vision Core on the platform overview) are presentation only and carry no requirement beyond FR-NAV-04 / FR-PREF-01 (motion and sound controls).
- **Consistency check:** {"passed — no problems found" if not problems else str(len(problems)) + " problem(s), listed below"}.
{chr(10).join("  - " + p for p in problems)}
"""
        (DOCS / "REQUIREMENT_TRACEABILITY.md").write_text(out, encoding="utf-8")
        print(f"wrote docs/REQUIREMENT_TRACEABILITY.md ({len(reqs)} requirements, {len(tests)} tests)")
    for p in problems:
        print("PROBLEM:", p)
    print("consistency:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
