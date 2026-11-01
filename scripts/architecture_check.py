"""Phase 5 architecture consistency checker and schema validator.

    python scripts/architecture_check.py                 # static checks (stdlib only); exit 1 on problems
    python scripts/architecture_check.py --write         # also regenerate the GENERATED sections and ARCHITECTURE_TRACEABILITY.md
    python scripts/architecture_check.py --sql sqlite    # + build the existing schema (Alembic) in a temp SQLite DB, apply the portable
                                                         #   reference DDL and run the constraint suite (needs the app's requirements)
    python scripts/architecture_check.py --sql postgresql+psycopg://...   # same against an EMPTY PostgreSQL database, including the
                                                         #   PostgreSQL-only alterations and guards

Sources of truth
  docs/architecture/architecture_catalog.json   components, table ownership, state machines, error codes, endpoints,
                                                 requirement ownership (hand-maintained)
  docs/architecture/schema/*.sql                 reference DDL (hand-maintained)
  docs/PRODUCT_REQUIREMENTS.md, DATA_CONTRACTS.md, API_INTEGRATION_REQUIREMENTS.md, ACCEPTANCE_TEST_PLAN.md (Phase 2)
  apps/api/** and apps/web/src/services/aero.js (actual code)
Nothing in apps/ is modified. The SQL mode only touches the database URL you pass, or a temporary SQLite file.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ARCH = DOCS / "architecture"
SCHEMA = ARCH / "schema"
API = ROOT / "apps" / "api" / "visentra_api"
CATALOG = ARCH / "architecture_catalog.json"
PORTABLE = ["010_new_tables.sql", "040_views_and_reference_data.sql"]
PG_ONLY = ["020_alter_existing_postgres.sql", "030_postgres_guards.sql"]
REQUIRED_DOCS = ["SYSTEM_ARCHITECTURE.md", "DATABASE_DESIGN.md", "API_ARCHITECTURE.md", "AI_INFERENCE_ARCHITECTURE.md",
                 "SECURITY_ARCHITECTURE.md", "RELIABILITY_AND_ERROR_HANDLING.md", "OBSERVABILITY_AND_OPERATIONS.md",
                 "DEPLOYMENT_ARCHITECTURE.md", "DATA_FLOW_AND_CONSISTENCY.md", "ARCHITECTURE_TRACEABILITY.md",
                 "PHASE_5_ARCHITECTURE_VALIDATION.md", "PHASE_5_COMPLETION.md"]
ADR_SECTIONS = ["## Status", "## Context", "## Options considered", "## Decision", "## Consequences"]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ============================================================================ parsing
def strip_sql_comments(sql: str) -> str:
    """Remove -- line comments outside quotes and $$ bodies (string literals may contain '--')."""
    out, i, in_q, in_dollar = [], 0, False, False
    while i < len(sql):
        if sql.startswith("$$", i) and not in_q:
            in_dollar = not in_dollar
            out.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == "'" and not in_dollar:
            in_q = not in_q
        if not in_q and not in_dollar and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = len(sql) if j < 0 else j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """Split on ';' outside $$...$$ bodies and quotes."""
    out, buf, i, in_dollar, in_q = [], [], 0, False, False
    while i < len(sql):
        if sql.startswith("$$", i) and not in_q:
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == "'" and not in_dollar:
            in_q = not in_q
        if ch == ";" and not in_dollar and not in_q:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def parse_ddl() -> dict:
    """tables -> {columns: {name: {"in": [...]|None}}, source: file}; alters; views; job kinds list."""
    tables: dict[str, dict] = {}
    views: list[str] = []
    job_kinds: list[str] = []
    altered: dict[str, list[str]] = {}
    for f in sorted(SCHEMA.glob("*.sql")):
        for st in split_statements(strip_sql_comments(read(f))):
            m = re.match(r"CREATE TABLE (\w+)\s*\((.*)\)\s*$", st, re.S)
            if m:
                name, body = m.group(1), m.group(2)
                cols = {}
                depth, cur, parts = 0, [], []
                for ch in body:
                    depth += ch == "("
                    depth -= ch == ")"
                    if ch == "," and depth == 0:
                        parts.append("".join(cur).strip())
                        cur = []
                    else:
                        cur.append(ch)
                parts.append("".join(cur).strip())
                for part in parts:
                    first = part.split()[0] if part.split() else ""
                    if first.upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK"):
                        continue
                    # a line may declare several columns (annotations box_*); split on ', <ident> <type>'
                    for sub in re.split(r",\s*(?=\w+\s+(?:double|varchar|integer|bigint|char|boolean|timestamptz|jsonb))", part):
                        cm = re.match(r"(\w+)\s+", sub.strip())
                        if not cm:
                            continue
                        col = cm.group(1)
                        im = re.search(r"CHECK \(\s*" + col + r"\s+IN\s*\(([^)]*)\)", sub)
                        cols[col] = {"in": re.findall(r"'([^']*)'", im.group(1)) if im else None}
                tables[name] = {"columns": cols, "source": f.name}
                continue
            m = re.match(r"CREATE (?:OR REPLACE )?VIEW (\w+)", st)
            if m:
                views.append(m.group(1))
                continue
            m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", st)
            if m:
                altered.setdefault(m.group(1), []).append(m.group(2))
            m = re.search(r"CONSTRAINT ck_jobs_kind CHECK \(kind IN \((.*)\)\)", st, re.S)
            if m:
                job_kinds = re.findall(r"'([^']*)'", m.group(1))
    return {"tables": tables, "views": views, "altered": altered, "job_kinds": job_kinds}


def existing_tables() -> set[str]:
    return set(re.findall(r'__tablename__ = "(\w+)"', read(API / "models.py")))


def existing_routes() -> set[tuple[str, str]]:
    routes = {("GET", "/api/health")}
    for f in (API / "routers").glob("*.py"):
        src = read(f)
        pm = re.search(r'APIRouter\(prefix="([^"]*)"', src)
        prefix = pm.group(1) if pm else ""
        for m in re.finditer(r'@router\.(get|post|put|patch|delete)\("([^"]*)"', src):
            routes.add((m.group(1).upper(), prefix + m.group(2)))
    return routes


def norm_path(p: str) -> str:
    p = re.sub(r"^/api/v1/", "/api/", p)
    return re.sub(r"\{[^}]+\}", "{}", p)


def existing_error_codes() -> set[str]:
    codes = set(re.findall(r'^([A-Z_]+) = "([A-Z_]+)"', read(API / "errors.py"), re.M))
    out = {c for _, c in codes}
    for f in [API / "main.py", API / "security.py", *(API / "routers").glob("*.py"), *(API / "services").glob("*.py"), *(API / "inspection").glob("*.py")]:
        out |= set(re.findall(r'"code":\s*"([A-Z_]+)"', read(f)))
        out |= set(re.findall(r'_payload\("([A-Z_]+)"', read(f)))
        out |= set(re.findall(r'(?:WORKER_LOST|code) = "([A-Z_]+)"', read(f)))
    return out


def existing_permissions() -> set[str]:
    return set(re.findall(r'"([a-z]+\.[a-z_]+)"', read(API / "permissions.py")))


def existing_job_kinds() -> set[str]:
    out = set()
    for f in API.rglob("*.py"):
        out |= set(re.findall(r'Job\([^)]*?kind="([a-z_]+)"', read(f), re.S))
    return out


def frontend_enums() -> dict[str, list[str]]:
    """Domain enumerations defined once in the frontend (NFR-MAINT-01). Phase 13: services/aero.js."""
    src = read(ROOT / "apps" / "web" / "src" / "services" / "aero.js")
    out = {}
    m = re.search(r"COMPONENTS: \[(.*?)\]\],", src, re.S)
    out["components"] = re.findall(r'\["([a-z_]+)",', m.group(1) + "]") if m else []
    out["categories"] = re.findall(r'\{ id: "([a-z_]+)", label: "[^"]+", hex:', src)
    for name, key in (("decisions", "DECISIONS"), ("statuses", "STATUS")):
        m = re.search(key + r": \{(.*?)\},\n", src, re.S)
        out[name] = re.findall(r"([a-z_]+): \[", m.group(1)) if m else []
    return out


def manifest_statuses() -> list[str]:
    m = re.search(r"STATUSES = \[(.*?)\]", read(ROOT / "scripts" / "datasets" / "validate_manifest.py"), re.S)
    return re.findall(r'"([A-Z_]+)"', m.group(1)) if m else []


def phase2():
    prd, atp = read(DOCS / "PRODUCT_REQUIREMENTS.md"), read(DOCS / "ACCEPTANCE_TEST_PLAN.md")
    reqs = {}
    for m in re.finditer(r"\*\*(FR-[A-Z]+-\d+) ([^*]+)\*\* — (P\d)[^—\n]*— ([^\n]+)", prd):
        st = m.group(4)
        reqs[m.group(1)] = {"title": m.group(2).strip(), "prio": m.group(3),
                            "status": next((k for k in ("NOT IMPLEMENTED", "PARTIAL", "BLOCKED", "VERIFIED", "N/A") if st.startswith(k)), "?")}
    for m in re.finditer(r"^\| (NFR-[A-Z0-9]+-\d+) \| ([^|]+) \| ([^|]+) \|[^|]*\| ([^|]+) \|", prd, re.M):
        st = m.group(4).strip()
        reqs[m.group(1)] = {"title": f"{m.group(2).strip()}: {m.group(3).strip()[:70]}", "prio": "NFR",
                            "status": next((k for k in ("NOT IMPLEMENTED", "PARTIAL", "BLOCKED", "VERIFIED", "N/A") if st.startswith(k)), st[:20])}

    def expand(text: str) -> set[str]:
        out = set()
        for m in re.finditer(r"\b(N?FR-[A-Z0-9]+-)(\d+(?:/\d+)*)", text):
            out |= {m.group(1) + n for n in m.group(2).split("/")}
        return out
    tests = {}
    for m in re.finditer(r"^\| (AT-\d+) \|[^\n]*$", atp, re.M):
        line = m.group(0)
        res = line.rstrip(" |").rsplit("|", 1)[-1].strip()
        for r in expand(line):
            tests.setdefault(r, []).append((m.group(1), "PASS" if res.startswith("PASS") else "future"))
    apis = set(re.findall(r"### (API-\d+)", read(DOCS / "API_INTEGRATION_REQUIREMENTS.md")))
    ents = set(re.findall(r"### (ENT-\d+)", read(DOCS / "DATA_CONTRACTS.md")))
    sys.path.insert(0, str(ROOT / "scripts"))
    import requirements_trace as RT  # the Phase 2 FR -> ENT/API mapping
    fr_map = {r: {"ents": re.findall(r"ENT-\d+", v[3]), "apis": re.findall(r"API-\d+", v[4])} for r, v in RT.MAP.items()}
    return reqs, tests, apis, ents, fr_map


# ============================================================================ static checks
def static_checks(cat: dict, ddl: dict) -> list[str]:
    P: list[str] = []
    comps = cat["components"]
    # components
    for cid, c in comps.items():
        for d in c["depends_on"]:
            if d not in comps:
                P.append(f"{cid}: depends on unknown component {d}")
        present = [p for p in c["code"] if (ROOT / p).exists()]
        if c["status"] == "existing" and len(present) != len(c["code"]):
            P.append(f"{cid}: status existing but {sorted(set(c['code']) - set(present))} do not exist")
        if c["status"] == "existing-extend" and not present:
            P.append(f"{cid}: status existing-extend but none of its code exists")
    # tables
    ex_tables = existing_tables()
    ddl_tables = set(ddl["tables"])
    for t, meta in cat["tables"].items():
        if meta["owner"] not in comps:
            P.append(f"table {t}: unknown owner {meta['owner']}")
        if meta["status"] == "planned" and t not in ddl_tables:
            P.append(f"table {t}: planned but not in the reference DDL")
        if meta["status"].startswith("existing") and t not in ex_tables:
            P.append(f"table {t}: marked {meta['status']} but not in models.py")
        if meta["status"] == "planned" and t in ex_tables:
            P.append(f"table {t}: marked planned but already exists in models.py")
        if meta["status"] == "existing-extended" and t not in ddl["altered"]:
            P.append(f"table {t}: marked existing-extended but the DDL adds no columns")
        if meta["status"] == "planned" and not meta["entities"]:
            P.append(f"table {t}: new table without a Phase 2 entity (unjustified)")
    for t in ddl_tables:
        if t not in cat["tables"]:
            P.append(f"DDL table {t} has no owner in the catalog")
    for t in ddl["altered"]:
        if t not in ex_tables:
            P.append(f"DDL alters {t}, which does not exist in models.py")
    # state machines vs DDL CHECK lists
    for key, sm in cat["state_machines"].items():
        t, col = key.split(".")
        states = set(sm["states"])
        for a, b, _ in sm["transitions"]:
            if a not in states or b not in states:
                P.append(f"{key}: transition {a}->{b} uses an undefined state")
        reach, frontier = set(sm["initial"]), list(sm["initial"])
        while frontier:
            s = frontier.pop()
            for a, b, _ in sm["transitions"]:
                if a == s and b not in reach:
                    reach.add(b)
                    frontier.append(b)
        if reach != states:
            P.append(f"{key}: unreachable states {sorted(states - reach)}")
        if sm["owner"] not in comps:
            P.append(f"{key}: unknown owner {sm['owner']}")
        if t in ddl["tables"]:
            ck = ddl["tables"][t]["columns"].get(col, {}).get("in")
            if ck is None:
                P.append(f"{key}: DDL has no CHECK ... IN list for {t}.{col}")
            elif set(ck) != states:
                P.append(f"{key}: DDL CHECK {sorted(ck)} != catalog states {sorted(states)}")
    for key, vals in cat["check_enums"].items():
        t, col = key.split(".")
        ck = ddl["tables"].get(t, {}).get("columns", {}).get(col, {}).get("in")
        if ck is None or set(ck) != set(vals):
            P.append(f"{key}: DDL CHECK {ck} != catalog {vals}")
    fe = frontend_enums()
    for key, fkey in (("inspections.component", "components"), ("human_findings.category", "categories"), ("finding_decisions.decision", "decisions")):
        if set(fe[fkey]) != set(cat["check_enums"][key]):
            P.append(f"{key} {cat['check_enums'][key]} != frontend {fkey} {fe[fkey]}")
    if not fe["statuses"] or not set(fe["statuses"]) <= set(cat["state_machines"]["inspections.status"]["states"]):
        P.append(f"frontend STATUS {fe['statuses']} is not a subset of inspections.status")
    ms = manifest_statuses()
    if set(ms) != set(cat["check_enums"]["dataset_sources.status"]):
        P.append(f"dataset_sources.status != Phase 3 STATUSES {ms}")
    # job kinds
    jk = cat["job_kinds"]
    if set(ddl["job_kinds"]) != set(jk):
        P.append(f"ck_jobs_kind {ddl['job_kinds']} != catalog job kinds {sorted(jk)}")
    code_kinds = existing_job_kinds()
    if code_kinds != {k for k, v in jk.items() if v["status"] == "existing"}:
        P.append(f"existing job kinds in code {sorted(code_kinds)} != catalog existing kinds")
    for k, v in jk.items():
        if v["status"] == "planned" and v.get("owner") not in comps:
            P.append(f"job kind {k}: unknown owner")
    # error codes
    ex_codes = existing_error_codes()
    for code, meta in cat["error_codes"].items():
        if meta["status"] == "existing" and code not in ex_codes:
            P.append(f"error code {code}: marked existing but not found in the code")
        if meta["status"] == "planned" and code in ex_codes:
            P.append(f"error code {code}: marked planned but already used in the code")
    for alias, target in cat["error_code_aliases"].items():
        if target not in cat["error_codes"]:
            P.append(f"alias {alias} -> unknown {target}")
    # endpoints
    routes = existing_routes()
    norm_routes = {(m, norm_path(p)) for m, p in routes}
    apis_seen = set()
    perms = existing_permissions() | set(cat["permissions_planned"]) | {"public", "authenticated"}
    for ep in cat["endpoints"]:
        key = (ep["method"], norm_path(ep["path"]))
        exists = key in norm_routes
        if ep["status"] in ("existing", "existing-extended") and not exists:
            P.append(f"{ep['id']}: marked {ep['status']} but {ep['method']} {ep['path']} is not a route in apps/api")
        if ep["status"] == "planned" and exists:
            P.append(f"{ep['id']}: marked planned but {ep['method']} {ep['path']} already exists")
        if ep["component"] not in comps:
            P.append(f"{ep['id']}: unknown component {ep['component']}")
        for e in ep["errors"]:
            if e not in cat["error_codes"]:
                P.append(f"{ep['id']}: error code {e} not in the catalog")
        perm = ep["permission"].split(" ")[0]
        if perm not in perms:
            P.append(f"{ep['id']}: unknown permission {perm}")
        if ep["api"]:
            apis_seen.add(ep["api"])
        if ep["method"] != "GET" and ep["status"] != "existing" and "idempotency" not in ep:
            P.append(f"{ep['id']}: mutation without an idempotency rule")
    # path ambiguity: a literal segment must not be shadowed by a {param} route at the same position
    plist = [(e["method"], e["path"]) for e in cat["endpoints"]]
    for m1, p1 in plist:
        for m2, p2 in plist:
            if m1 == m2 and p1 != p2:
                a, b = p1.strip("/").split("/"), p2.strip("/").split("/")
                if len(a) == len(b) and all(x == y or (y.startswith("{") and not x.startswith("{")) for x, y in zip(a, b)) and a != b:
                    P.append(f"route ambiguity: {m1} {p1} is shadowed by {p2}")
    for pp in cat["permissions_planned"]:
        if pp in existing_permissions():
            P.append(f"permission {pp} marked planned but already in permissions.py")
    # Phase 2 coverage
    reqs, tests, apis, ents, fr_map = phase2()
    for r in reqs:
        if r not in cat["requirements"] or not cat["requirements"][r]:
            P.append(f"{r}: no accountable component")
    for r, cs in cat["requirements"].items():
        if r not in reqs:
            P.append(f"{r}: in catalog but not in PRODUCT_REQUIREMENTS")
        for c in cs:
            if c not in comps:
                P.append(f"{r}: unknown component {c}")
    for a in apis:
        if a not in apis_seen:
            P.append(f"{a}: no endpoint in the catalog")
    ent_tables = {e for t in cat["tables"].values() for e in t["entities"]}
    for e in ents | {"ENT-08", "ENT-15", "ENT-16"}:
        if e not in ent_tables:
            P.append(f"{e}: no table realises it")
    # docs
    for d in REQUIRED_DOCS:
        if not (DOCS / d).exists():
            P.append(f"missing document docs/{d}")
    adrs = sorted((DOCS / "adr").glob("ADR-*.md"))
    if len(adrs) < 8:
        P.append(f"only {len(adrs)} ADRs found in docs/adr")
    for a in adrs:
        txt = read(a)
        for s in ADR_SECTIONS:
            if s not in txt:
                P.append(f"{a.name}: missing section '{s}'")
    for d in REQUIRED_DOCS + [a.relative_to(DOCS).as_posix() for a in adrs]:
        p = DOCS / d
        if p.exists():
            for blk in re.findall(r"```mermaid\n(.*?)```", read(p), re.S):
                if not re.match(r"\s*(flowchart|graph|sequenceDiagram|erDiagram|stateDiagram-v2|classDiagram)\b", blk):
                    P.append(f"{d}: mermaid block without a known diagram type")
    return P


# ============================================================================ generated sections
def gen_sections(cat: dict, ddl: dict) -> dict[str, str]:
    out = {}
    eps = cat["endpoints"]
    lines = ["| Id | API | Method | Path | Owner | Permission | Idempotency | Success | Errors | Release | Status |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in eps:
        lines.append(f"| {e['id']} | {e['api'] or '—'} | {e['method']} | `{e['path']}` | {e['component']} | `{e['permission']}` | {e['idempotency']} | {e['success']} | {', '.join(f'`{x}`' for x in e['errors']) or '—'} | {e['release']} | {e['status']} |")
    out["endpoints"] = "\n".join(lines)
    lines = ["| Code | HTTP | Status | Client retry | Meaning |", "|---|---|---|---|---|"]
    for c, m in cat["error_codes"].items():
        lines.append(f"| `{c}` | {m['http'] or '— (stored on run/job)'} | {m['status']} | {m['retry']} | {m['meaning']} |")
    lines.append("")
    lines.append("Aliases for names used in Phase 2 documents: " + ", ".join(f"`{a}` → `{b}`" for a, b in cat["error_code_aliases"].items()) + ".")
    out["errors"] = "\n".join(lines)
    lines = []
    for key, sm in cat["state_machines"].items():
        lines.append(f"**`{key}`** (owner {sm['owner']}{', existing' if sm.get('existing') else ''}). States: " + ", ".join(f"`{s}`" for s in sm["states"]) + f". Initial: {', '.join(sm['initial'])}.")
        lines.append("")
        lines.append("| From | To | Trigger |")
        lines.append("|---|---|---|")
        for a, b, why in sm["transitions"]:
            lines.append(f"| `{a}` | `{b}` | {why} |")
        lines.append("")
        lines.append("Any transition not listed is rejected (`409`).")
        lines.append("")
    out["state-machines"] = "\n".join(lines).rstrip()
    lines = ["| Table | Status | Release | Owner | Realises | Defined in |", "|---|---|---|---|---|---|"]
    for t, m in cat["tables"].items():
        src = ddl["tables"][t]["source"] if t in ddl["tables"] else ("020_alter_existing_postgres.sql" if t in ddl["altered"] else "existing (models.py)")
        lines.append(f"| `{t}` | {m['status']} | {m['release']} | {m['owner']} | {', '.join(m['entities']) or 'infrastructure'} | {src} |")
    out["tables"] = "\n".join(lines)
    lines = ["| Kind | Status | Owner | Timeout (s) | Automatic retries | Note |", "|---|---|---|---|---|---|"]
    for k, m in cat["job_kinds"].items():
        lines.append(f"| `{k}` | {m['status']} | {m.get('owner', 'C-PLATFORM')} | {m['timeout_s'] or '—'} | {m['retries']} | {m.get('note', '')} |")
    out["job-kinds"] = "\n".join(lines)
    lines = ["| Id | Component | Status | Code | Depends on | Responsibility |", "|---|---|---|---|---|---|"]
    for cid, c in cat["components"].items():
        lines.append(f"| {cid} | {c['name']} | {c['status']} | {', '.join(f'`{p}`' for p in c['code'])} | {', '.join(c['depends_on']) or '—'} | {c['summary']} |")
    out["components"] = "\n".join(lines)
    return out


def traceability(cat: dict) -> str:
    reqs, tests, apis, ents, fr_map = phase2()
    ent_to_tables: dict[str, list[str]] = {}
    for t, m in cat["tables"].items():
        for e in m["entities"]:
            ent_to_tables.setdefault(e, []).append(t)
    api_to_eps: dict[str, list[str]] = {}
    for e in cat["endpoints"]:
        if e["api"]:
            api_to_eps.setdefault(e["api"], []).append(e["id"])
    rel = {}
    for line in re.findall(r"^\| (R\d) [^|]*\| ([^|]+) \|", read(DOCS / "PRODUCT_REQUIREMENTS.md"), re.M):
        for r in re.findall(r"N?FR-[A-Z0-9]+-\d+(?:/\d+)*", line[1]):
            base = r.rsplit("-", 1)[0]
            for n in r.rsplit("-", 1)[1].split("/"):
                rel.setdefault(f"{base}-{n}", line[0])
    comps = cat["components"]
    L = ["# VisionTra — Architecture Traceability", "",
         "Generated by `python scripts/architecture_check.py --write` from `docs/architecture/architecture_catalog.json`, "
         "the Phase 2 documents (requirements, entity and API mappings in `scripts/requirements_trace.py`, acceptance tests) and the reference DDL. "
         "**Do not edit by hand.** The current status is copied from PRODUCT_REQUIREMENTS.md. Planned components and endpoints are designs, not running code.", "",
         "Legend: component status `existing` = in the repository and tested · `existing-extend` = exists, Phase 5 design extends it · `planned` = designed, not built. "
         "Tests: PASS = executed 2026-10-09 (Phase 2) · future = cannot run until the component exists.", "",
         "## 1. Requirement → architecture", "",
         "| Req | Title | Prio | Current status | Release | Components | Tables | Endpoints | Security constraint | Failure handling | Tests |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    sec = {"C-INSP": "inspection.* + workspace scope (404)", "C-REVIEW": "inspection.review, If-Match", "C-MEDIA": "media.read / read_original, content validation",
           "C-REPORT": "report.create/read", "C-DATA": "dataset.read/write", "C-MODELREG": "model.* (promote: owner/admin)", "C-ANALYSIS": "inspection.create",
           "C-RBAC": "server-side permission check", "C-AUTH": "session + CSRF", "C-RET": "settings.manage", "C-ANL": "inspection.read"}
    fail = {"C-INSP": "conditional UPDATE → REVIEW_CONFLICT", "C-REVIEW": "unique revision → REVIEW_CONFLICT", "C-MEDIA": "MEDIA_INVALID / STORAGE_UNAVAILABLE, intent rollback",
            "C-ANALYSIS": "run failed + error_code, retry = new attempt", "C-REPORT": "report failed, 1 retry on storage", "C-STORE": "storage_objects reconciler",
            "C-JOBS": "WORKER_LOST reconciliation", "C-RET": "idempotent purge + tombstone", "C-DATA": "import job atomic per version", "C-MODELREG": "checksum gate, rollback"}
    for r, m in reqs.items():
        cs = cat["requirements"].get(r, [])
        mp = fr_map.get(r, {"ents": [], "apis": []})
        tables = sorted({t for e in mp["ents"] for t in ent_to_tables.get(e, [])})
        eps = sorted({x for a in mp["apis"] for x in api_to_eps.get(a, [])})
        ts = ", ".join(f"{t} ({res})" for t, res in sorted(tests.get(r, []), key=lambda x: int(x[0][3:]))) or "—"
        L.append(f"| {r} | {m['title']} | {m['prio']} | {m['status']} | {rel.get(r, '—')} | "
                 f"{', '.join(c + ' (' + comps[c]['status'] + ')' for c in cs)} | {', '.join(f'`{t}`' for t in tables) or '—'} | {', '.join(eps) or '—'} | "
                 f"{'; '.join(sorted({sec[c] for c in cs if c in sec})) or '—'} | {'; '.join(sorted({fail[c] for c in cs if c in fail})) or '—'} | {ts} |")
    # gap analysis
    used_tables = {t for r in reqs for e in fr_map.get(r, {}).get("ents", []) for t in ent_to_tables.get(e, [])}
    L += ["", "## 2. Gap analysis (computed)", ""]
    no_owner = [r for r in reqs if not cat["requirements"].get(r)]
    L.append(f"- Requirements without an accountable component: {', '.join(no_owner) or '**none**'}.")
    orphan = [t for t, m in cat["tables"].items() if m["status"] == "planned" and t not in used_tables]
    L.append(f"- New tables not reached through a requirement's entity mapping: {', '.join(f'`{t}`' for t in orphan) or '**none**'}"
             + (" (justified by the DATASET_AND_ML requirements DSP/DSQ/SPL/MLG, which `requirements_trace.py` does not map to entities)." if orphan else "."))
    no_ep_owner = [e["id"] for e in cat["endpoints"] if e["component"] not in comps]
    L.append(f"- Endpoints without an owning component: {', '.join(no_ep_owner) or '**none**'}.")
    no_api = sorted(a for a in apis if a not in api_to_eps)
    L.append(f"- Phase 2 operations (API-*) without an endpoint: {', '.join(no_api) or '**none**'}.")
    L.append("- Storage operations and their recovery policy: every object written by a new module is registered in `storage_objects` "
             "(`pending` → `committed`); the `storage_reconcile` job removes orphan intents and flags `missing` objects (DATA_FLOW_AND_CONSISTENCY.md §3). "
             "Objects written by the existing classification platform (`images`, `predictions`, model artefacts) are not registered. Gap G-01, accepted: their lifecycle is unchanged.")
    L.append("- Sensitive data and its access control: original media (`media.read_original`, VIEW role excluded), reports (`report.read`), "
             "EXIF capture fields (no GPS stored), audit log (`audit.read`), model artefacts (separate store, `model.read` for metadata only). See SECURITY_ARCHITECTURE.md §3.")
    L.append("- Model outputs and their provenance: `defect_predictions` has a composite FK to `analysis_runs(id, inspection_id, model_version_id)`; "
             "each run stores the verified model checksum, taxonomy version and thresholds; `analysis_media` stores the preprocessing parameters. Without a model, no row exists.")
    L.append("- Existing integrations that the design touches (all additive, regression-tested on PostgreSQL; see PHASE_5_ARCHITECTURE_VALIDATION.md): "
             "`jobs` (nullable project_id + kind CHECK), `model_versions`, `evaluation_runs`, `dataset_versions` (new columns with classification defaults), "
             "`/api/media/{kind}` (new kind), `/api/system/capabilities`, `/api/health/ready`, `/api/events` (new fields), `audit_events` (immutability trigger).")
    blocked = [r for r, m in reqs.items() if m["status"] == "BLOCKED"]
    L.append(f"- Requirements blocked by missing data or permission: {', '.join(blocked)} (no trained detection model). FR-INF-03/FR-DET-02/FR-MDL-03/FR-MDL-05 also depend on it. "
             "Training for crack and corrosion is blocked by the UNRESOLVED class mappings (Phase 4 CMAP-001). Aircraft-independent corrosion evaluation is blocked by a single-aircraft source.")
    L += ["", "## 3. Component → requirements", "", "| Component | Status | Requirements |", "|---|---|---|"]
    for cid, c in comps.items():
        rs = sorted(r for r, cs in cat["requirements"].items() if cid in cs)
        L.append(f"| {cid} {c['name']} | {c['status']} | {', '.join(rs) or '— (infrastructure)'} |")
    return "\n".join(L) + "\n"


def apply_generated(write: bool, cat: dict, ddl: dict) -> list[str]:
    P = []
    secs = gen_sections(cat, ddl)
    for d in REQUIRED_DOCS:
        p = DOCS / d
        if not p.exists():
            continue
        txt = read(p)
        new = txt
        for name, body in secs.items():
            new = re.sub(rf"(<!-- GENERATED:{name} START -->\n).*?(\n<!-- GENERATED:{name} END -->)", lambda m: m.group(1) + body + m.group(2), new, flags=re.S)
        if new != txt:
            if write:
                p.write_text(new, encoding="utf-8")
            else:
                P.append(f"docs/{d}: GENERATED sections are out of date (run with --write)")
    tr = traceability(cat)
    tp = DOCS / "ARCHITECTURE_TRACEABILITY.md"
    if write:
        tp.write_text(tr, encoding="utf-8")
    elif not tp.exists() or read(tp) != tr:
        P.append("docs/ARCHITECTURE_TRACEABILITY.md is out of date (run with --write)")
    return P


# ============================================================================ SQL validation
def sql_mode(url: str) -> int:
    """Re-exec in a clean process so the app's settings pick up the target DATABASE_URL."""
    tmp = tempfile.mkdtemp(prefix="vt-arch-")
    if url == "sqlite":
        url = f"sqlite:///{tmp}/arch.db"
    env = {**os.environ, "DATABASE_URL": url, "VISENTRA_DATA_DIR": tmp, "VISENTRA_ENV": "test", "WORKER_MODE": "external",
           "JWT_SECRET": "arch-check-" + "x" * 40, "LOG_LEVEL": "WARNING"}
    return subprocess.call([sys.executable, str(Path(__file__).resolve()), "--_sql-child"], env=env, cwd=str(ROOT / "apps" / "api"))


def sql_child() -> int:
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    sys.path.insert(0, str(ROOT / "ml"))
    from sqlalchemy import text
    from visentra_api.main import migrate
    from visentra_api.db import engine, SessionLocal
    from visentra_api import models as M
    pg = engine.dialect.name == "postgresql"
    with engine.connect() as c:
        if c.execute(text("SELECT count(*) FROM " + ("information_schema.tables WHERE table_schema='public'" if pg else "sqlite_master WHERE type='table'"))).scalar():
            print("ERROR: the target database must be empty (the checker builds the existing schema with Alembic first).")
            return 2
    migrate()
    files = PORTABLE[:1] + (PG_ONLY[:1] if pg else []) + PORTABLE[1:] + (PG_ONLY[1:] if pg else [])
    from sqlalchemy import inspect as sa_inspect
    built = set(sa_inspect(engine).get_table_names())        # tables Alembic already created (implemented releases)
    ddl_new = {m.group(1) for f in files for st in split_statements(strip_sql_comments(read(SCHEMA / f))) for m in [re.match(r"CREATE TABLE (\w+)", st)] if m}
    built &= ddl_new                                          # only reference-DDL tables that a migration now implements
    realised: set[str] = set()
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for f in files:
            for st in split_statements(strip_sql_comments(read(SCHEMA / f))):
                tm = re.match(r"CREATE TRIGGER (\w+) ", st)
                if tm:                                # a guard the migration already installed (same name) is not created twice
                    cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s" if pg else "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name = ?", (tm.group(1),))
                    if cur.fetchone()[0]:
                        realised.add("trigger " + tm.group(1))
                        continue
                m = re.match(r"CREATE TABLE (\w+)", st) or re.match(r"CREATE (?:UNIQUE )?INDEX \w+\s+ON\s+(\w+)", st)
                if m and m.group(1) in built:
                    realised.add(m.group(1))      # implemented by a migration: the constraint suite below tests that implementation
                    continue
                cur.execute(st)
        raw.commit()
    finally:
        raw.close()
    print(f"applied {', '.join(files)} on {engine.dialect.name}; realised by Alembic (reference DDL skipped): {', '.join(sorted(realised)) or 'none'}")
    # ---------------------------------------------------------------- fixtures through the ORM (Python-side defaults)
    db = SessionLocal()
    u1, u2 = M.User(email="a@x.org", name="A", password_hash="x"), M.User(email="b@x.org", name="B", password_hash="x")
    db.add_all([u1, u2]); db.flush()
    ws = M.Workspace(name="W", owner_id=u1.id); db.add(ws); db.flush()
    p = M.Project(name="det", owner_id=u1.id, workspace_id=ws.id, project_type="object_detection"); db.add(p); db.flush()
    ds = M.Dataset(project_id=p.id, name="d"); db.add(ds); db.flush()
    dv = M.DatasetVersion(dataset_id=ds.id, project_id=p.id, version=1, seed=1, val_ratio=.15, test_ratio=.15, created_by=u1.id); db.add(dv); db.flush()
    job = M.Job(project_id=p.id, kind="train", created_by=u1.id); db.add(job); db.flush()
    ex = M.Experiment(project_id=p.id, job_id=job.id, dataset_version_id=dv.id, seed=1, created_by=u1.id); db.add(ex); db.flush()
    mv1 = M.ModelVersion(project_id=p.id, experiment_id=ex.id, dataset_version_id=dv.id, version=1, architecture="det", seed=1, status="COMPLETED")
    mv2 = M.ModelVersion(project_id=p.id, experiment_id=ex.id, dataset_version_id=dv.id, version=2, architecture="det", seed=1, status="COMPLETED")
    db.add_all([mv1, mv2]); db.commit()
    ids = {"u1": u1.id, "u2": u2.id, "ws": ws.id, "mv1": mv1.id, "mv2": mv2.id, "dv": dv.id, "job": job.id, "p": p.id}
    db.close()
    H = "a" * 64
    NOW = "'2026-10-11T00:00:00+00:00'"

    def q(s: str) -> str:
        for k, v in ids.items():
            s = s.replace("{" + k + "}", v)
        return s.replace("{H}", H).replace("{NOW}", NOW)

    setup = [
        "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('i1','{ws}','INS-2026-000001','fuselage','awaiting_review','{u1}')",
        "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('i2','{ws}','INS-2026-000002','wing_upper','awaiting_review','{u1}')",
        "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) VALUES ('m1','{ws}','i1',1,'image','a.jpg','image/jpeg',10,640,480,'{H}','media/a1','{u1}')",
        "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) VALUES ('m2','{ws}','i2',1,'image','b.jpg','image/jpeg',10,640,480,'{H}','media/b1','{u1}')",
        "INSERT INTO analysis_runs (id, inspection_id, workspace_id, attempt, model_version_id, model_checksum, taxonomy_version, score_threshold, iou_threshold, review_threshold, status, requested_by, finished_at) VALUES ('r1','i1','{ws}',1,'{mv1}','{H}','TAX-1.0',0.25,0.5,0.5,'succeeded','{u1}',{NOW})",
        "INSERT INTO analysis_media (analysis_run_id, media_id, oriented_width, oriented_height, input_width, input_height, resize_mode, scale_x, scale_y, pad_x, pad_y, status, n_predictions) VALUES ('r1','m1',640,480,640,640,'letterbox',1,1,0,80,'succeeded',2)",
        "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('pr1','i1','r1','{mv1}','m1',1,'dent',1,'TAX-1.0',0.9,0.1,0.1,0.2,0.2,'[1,2,3,4]',false)",
        "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('pr2','i1','r1','{mv1}','m1',2,'crack',0,'TAX-1.0',0.3,0.5,0.5,0.1,0.1,'[1,2,3,4]',true)",
        "INSERT INTO human_findings (id, inspection_id, media_id, category, taxonomy_version, box_x, box_y, box_w, box_h, created_by) VALUES ('hf1','i1','m1','other','TAX-1.0',0.6,0.6,0.1,0.1,'{u2}')",
        "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, actor_id, inspection_revision) VALUES ('d1','i1','prediction','pr1','decision','confirmed','{u2}',1)",
        "INSERT INTO finding_decisions (id, inspection_id, target_type, human_finding_id, kind, decision, actor_id, inspection_revision) VALUES ('d2','i1','human_finding','hf1','decision','confirmed','{u2}',2)",
        "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, comment, actor_id, inspection_revision) VALUES ('d3','i1','prediction','pr1','note','looks like hail','{u2}',3)",
        "UPDATE inspections SET status='in_review', revision=3 WHERE id='i1'",
        "INSERT INTO taxonomy_classes (taxonomy_version, class_id, display_name, trainable, definition_ref) VALUES ('TAX-T','dent','Dent',true,'x')",
        "INSERT INTO dataset_sources (id, external_id, name, publisher, landing_url, domain, licence_name, licence_status, licence_evidence, status) VALUES ('s1','VT-DS-001','IH','IH','https://x','aircraft','CC BY 4.0','VERIFIED','[\"https://x\"]','INTEGRITY_VERIFIED')",
        "INSERT INTO data_processing_runs (id, external_id, kind, tool, status, started_at, finished_at) VALUES ('run1','VT-PREP-001','preparation','run_prep.py','succeeded',{NOW},{NOW})",
        "INSERT INTO dataset_media (id, source_id, source_file_id, sha256, raw_path, mime, width, height, registered_by_run) VALUES ('dm1','s1','VT-DS-001:train/images/a.jpg','{H}','datasets/raw/VT-DS-001/train/images/a.jpg','image/jpeg',640,640,'run1')",
        "INSERT INTO annotation_sets (id, source_id, kind, label_format, class_names, created_by_run) VALUES ('as1','s1','source_original','yolo','[\"crack\",\"dent\"]','run1')",
        "INSERT INTO audit_events (id, at, action, resource_type, result, meta) VALUES ('ae1',{NOW},'inspection.created','inspection','success','{}')",
    ]
    ok_cases = [
        ("view: current decisions for i1 = 2 targets", "SELECT count(*) FROM v_current_finding_decisions WHERE inspection_id='i1'", 2),
        ("view: pending findings for i1 = pr2 only", "SELECT count(*) FROM v_pending_findings WHERE inspection_id='i1'", 1),
        ("view: current predictions for i1 = 2", "SELECT count(*) FROM v_current_predictions WHERE inspection_id='i1'", 2),
        ("TAX-1.0 seeded with 4 classes (1 not trainable)", "SELECT count(*) FROM taxonomy_classes WHERE taxonomy_version='TAX-1.0' AND trainable = false", 1),
        ("reclassify of pr2 to corrosion (confirmed + corrected class)", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, corrected_class_id, actor_id, inspection_revision) VALUES ('d4','i1','prediction','pr2','reclassify','confirmed','corrosion','{u2}',4)", None),
        ("view: no pending findings after reclassify", "SELECT count(*) FROM v_pending_findings WHERE inspection_id='i1'", 0),
        ("sign-off with all fields", "UPDATE inspections SET status='reviewed', revision=5, signed_off_at={NOW}, signed_off_by='{u2}', signoff_outcome='findings_recorded' WHERE id='i1'", None),
        ("sign-off event", "INSERT INTO finding_decisions (id, inspection_id, target_type, kind, actor_id, inspection_revision) VALUES ('d5','i1','inspection','signoff','{u2}',5)", None),
        ("report available with checksum and size", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, analysis_run_id, model_version_id, object_key, sha256, size_bytes, pages, requested_by, generated_at) VALUES ('rep1','i1',5,'RPT-1','available','r1','{mv1}','reports/x.pdf','{H}',1000,2,'{u2}',{NOW})", None),
        ("failed report for the same revision does not block a new one", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, error_code, requested_by) VALUES ('rep0','i1',5,'RPT-1','failed','REPORT_GENERATION_FAILED','{u2}')", None),
        ("retry analysis attempt 2 queued on i2 is allowed", "INSERT INTO analysis_runs (id, inspection_id, workspace_id, attempt, model_version_id, taxonomy_version, score_threshold, iou_threshold, review_threshold, status, requested_by) VALUES ('r2','i2','{ws}',1,'{mv1}','TAX-1.0',0.25,0.5,0.5,'queued','{u1}')", None),
        ("unresolved mapping may name a candidate target", "INSERT INTO class_mappings (id, mapping_version, taxonomy_version, source_id, source_class_index, source_class_name, target_class_id, status, rationale) VALUES ('cm1','CMAP-001','TAX-1.0','s1',1,'Rust','corrosion','UNRESOLVED','candidate only')", None),
        ("empty-label image stays PENDING_REVIEW without reviewer", "INSERT INTO annotation_image_status (annotation_set_id, dataset_media_id, label_file_state, review_status) VALUES ('as1','dm1','EMPTY_LABEL_FILE','PENDING_REVIEW')", None),
        ("committed storage object with owner and checksum", "INSERT INTO storage_objects (object_key, store, purpose, state, sha256, size_bytes, owner_type, owner_id, committed_at) VALUES ('media/a1','objects','media_original','committed','{H}',10,'inspection_media','m1',{NOW})", None),
    ]
    bad_cases = [
        ("unknown inspection status", "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('x1','{ws}','INS-2026-000009','fuselage','done','{u1}')"),
        ("unknown component", "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('x1','{ws}','INS-2026-000009','engine','created','{u1}')"),
        ("reviewed without sign-off fields", "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('x1','{ws}','INS-2026-000009','fuselage','reviewed','{u1}')"),
        ("analysis_failed without error code", "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('x1','{ws}','INS-2026-000009','fuselage','analysis_failed','{u1}')"),
        ("duplicate reference in workspace", "INSERT INTO inspections (id, workspace_id, ref, component, status, created_by) VALUES ('x1','{ws}','INS-2026-000001','fuselage','created','{u1}')"),
        ("video frame without parent", "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) VALUES ('x2','{ws}','i1',2,'video_frame','f.jpg','image/jpeg',10,64,64,'{H}','media/x2','{u1}')"),
        ("short sha256", "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) VALUES ('x2','{ws}','i1',2,'image','f.jpg','image/jpeg',10,64,64,'abc','media/x2','{u1}')"),
        ("unsupported mime", "INSERT INTO inspection_media (id, workspace_id, inspection_id, ordinal, kind, filename, mime, size_bytes, width, height, sha256, object_key, uploaded_by) VALUES ('x2','{ws}','i1',2,'image','f.gif','image/gif',10,64,64,'{H}','media/x2','{u1}')"),
        ("second active analysis run", "INSERT INTO analysis_runs (id, inspection_id, workspace_id, attempt, model_version_id, taxonomy_version, score_threshold, iou_threshold, review_threshold, status, requested_by) VALUES ('x3','i2','{ws}',2,'{mv1}','TAX-1.0',0.25,0.5,0.5,'queued','{u1}')"),
        ("failed run without error code", "INSERT INTO analysis_runs (id, inspection_id, workspace_id, attempt, model_version_id, taxonomy_version, score_threshold, iou_threshold, review_threshold, status, requested_by, finished_at) VALUES ('x3','i1',2,'{mv1}','TAX-1.0',0.25,0.5,0.5,'failed','{u1}',{NOW})".replace("'i1',2", "'i1','{ws}',2")),
        ("succeeded run without verified checksum", "INSERT INTO analysis_runs (id, inspection_id, workspace_id, attempt, model_version_id, taxonomy_version, score_threshold, iou_threshold, review_threshold, status, requested_by, finished_at) VALUES ('x3','i1','{ws}',2,'{mv1}','TAX-1.0',0.25,0.5,0.5,'succeeded','{u1}',{NOW})"),
        ("prediction claims a model version its run did not use", "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('x4','i1','r1','{mv2}','m1',9,'dent',1,'TAX-1.0',0.9,0.1,0.1,0.2,0.2,'[]',false)"),
        ("prediction on media of another inspection", "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('x4','i1','r1','{mv1}','m2',9,'dent',1,'TAX-1.0',0.9,0.1,0.1,0.2,0.2,'[]',false)"),
        ("box outside the image", "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('x4','i1','r1','{mv1}','m1',9,'dent',1,'TAX-1.0',0.9,0.9,0.1,0.2,0.2,'[]',false)"),
        ("zero-width box", "INSERT INTO human_findings (id, inspection_id, media_id, category, taxonomy_version, box_x, box_y, box_w, box_h, created_by) VALUES ('x4','i1','m1','dent','TAX-1.0',0.1,0.1,0,0.2,'{u2}')"),
        ("score above 1", "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('x4','i1','r1','{mv1}','m1',9,'dent',1,'TAX-1.0',1.5,0.1,0.1,0.2,0.2,'[]',false)"),
        ("duplicate prediction ordinal in a run (retry persistence)", "INSERT INTO defect_predictions (id, inspection_id, analysis_run_id, model_version_id, media_id, ordinal, class_id, model_class_index, taxonomy_version, score, box_x, box_y, box_w, box_h, box_input_xyxy, low_confidence) VALUES ('x4','i1','r1','{mv1}','m1',1,'dent',1,'TAX-1.0',0.9,0.1,0.1,0.2,0.2,'[]',false)"),
        ("human finding with a non-taxonomy category", "INSERT INTO human_findings (id, inspection_id, media_id, category, taxonomy_version, box_x, box_y, box_w, box_h, created_by) VALUES ('x4','i1','m1','scratch','TAX-1.0',0.1,0.1,0.1,0.1,'{u2}')"),
        ("decision event without a decision value", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, actor_id, inspection_revision) VALUES ('x5','i1','prediction','pr2','decision','{u2}',90)"),
        ("human finding set back to pending", "INSERT INTO finding_decisions (id, inspection_id, target_type, human_finding_id, kind, decision, actor_id, inspection_revision) VALUES ('x5','i1','human_finding','hf1','decision','pending','{u2}',90)"),
        ("reclassify that is not a confirmation", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, corrected_class_id, actor_id, inspection_revision) VALUES ('x5','i1','prediction','pr2','reclassify','rejected','dent','{u2}',90)"),
        ("reclassify a human finding", "INSERT INTO finding_decisions (id, inspection_id, target_type, human_finding_id, kind, decision, corrected_class_id, actor_id, inspection_revision) VALUES ('x5','i1','human_finding','hf1','reclassify','confirmed','dent','{u2}',90)"),
        ("two events for the same revision (concurrent reviewers)", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, actor_id, inspection_revision) VALUES ('x5','i1','prediction','pr2','decision','rejected','{u2}',1)"),
        ("sign-off event aimed at a prediction", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, actor_id, inspection_revision) VALUES ('x5','i1','prediction','pr2','signoff','{u2}',90)"),
        ("decision referencing a prediction of another inspection", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, decision, actor_id, inspection_revision) VALUES ('x5','i2','prediction','pr1','decision','confirmed','{u2}',1)"),
        ("note without text", "INSERT INTO finding_decisions (id, inspection_id, target_type, prediction_id, kind, actor_id, inspection_revision) VALUES ('x5','i1','prediction','pr2','note','{u2}',90)"),
        ("available report without checksum", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, object_key, size_bytes, requested_by, generated_at) VALUES ('x6','i1',6,'RPT-1','available','reports/y.pdf',10,'{u2}',{NOW})"),
        ("report containing SAMPLE data", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, contains_sample, requested_by) VALUES ('x6','i1',6,'RPT-1','queued',true,'{u2}')"),
        ("second live report for the same revision and template", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, requested_by) VALUES ('x6','i1',5,'RPT-1','queued','{u2}')"),
        ("report naming a run without its model", "INSERT INTO inspection_reports (id, inspection_id, inspection_revision, template_version, status, analysis_run_id, requested_by) VALUES ('x6','i1',6,'RPT-1','queued','r1','{u2}')"),
        ("confirmed negative without a qualified reviewer", "UPDATE annotation_image_status SET review_status='CONFIRMED_NEGATIVE', reviewer='Claude (AI agent)', reviewed_at={NOW}, evidence='screen' WHERE dataset_media_id='dm1'"),
        ("accepted mapping without a decision record", "INSERT INTO class_mappings (id, mapping_version, taxonomy_version, source_id, source_class_index, source_class_name, target_class_id, status, rationale) VALUES ('x7','CMAP-001','TAX-1.0','s1',0,'crack','crack','ACCEPTED','x')"),
        ("mapping to a class outside the taxonomy", "INSERT INTO class_mappings (id, mapping_version, taxonomy_version, source_id, source_class_index, source_class_name, target_class_id, status, rationale, decided_by, decided_at) VALUES ('x7','CMAP-001','TAX-1.0','s1',0,'scratch','scratch','ACCEPTED','x','lead',{NOW})"),
        ("not-mapped class with a target", "INSERT INTO class_mappings (id, mapping_version, taxonomy_version, source_id, source_class_index, source_class_name, target_class_id, status, rationale) VALUES ('x7','CMAP-001','TAX-1.0','s1',2,'paint-off','corrosion','NOT_MAPPED','x')"),
        ("verified licence without evidence", "INSERT INTO dataset_sources (id, external_id, name, publisher, landing_url, domain, licence_name, licence_status, status) VALUES ('x8','VT-DS-099','n','p','u','aircraft','CC BY 4.0','VERIFIED','DISCOVERED')"),
        ("retained dataset item without a split", "INSERT INTO detection_version_items (dataset_version_id, dataset_media_id, annotation_set_id, split_group, inclusion) VALUES ('{dv}','dm1','as1','SG-1','RETAINED')"),
        ("quarantined dataset item with a split", "INSERT INTO detection_version_items (dataset_version_id, dataset_media_id, annotation_set_id, split, split_group, inclusion) VALUES ('{dv}','dm1','as1','test','SG-1','QUARANTINED')"),
        ("corrected annotation set without parent", "INSERT INTO annotation_sets (id, source_id, kind, label_format, class_names, created_by_run) VALUES ('x9','s1','corrected','yolo','[]','run1')"),
        ("committed storage object without checksum", "INSERT INTO storage_objects (object_key, store, purpose, state, owner_type, owner_id, committed_at) VALUES ('k/x','objects','report','committed','inspection_reports','rep1',{NOW})"),
        ("deleted storage object without deletion time", "INSERT INTO storage_objects (object_key, store, purpose, state) VALUES ('k/y','objects','report','deleted')"),
    ]
    pg_cases = [
        ("predictions are immutable", "UPDATE defect_predictions SET score=0.1 WHERE id='pr1'", "VT_IMMUTABLE"),
        ("decision history is append-only", "DELETE FROM finding_decisions WHERE id='d1'", "VT_IMMUTABLE"),
        ("media evidence fields are immutable", "UPDATE inspection_media SET sha256='{H}'||'' , object_key='media/zz' WHERE id='m1'", "VT_IMMUTABLE"),
        ("media rows are never deleted", "DELETE FROM inspection_media WHERE id='m2'", "VT_IMMUTABLE"),
        ("available report is frozen", "UPDATE inspection_reports SET object_key='reports/other.pdf' WHERE id='rep1'", "VT_IMMUTABLE"),
        ("audit events are append-only", "UPDATE audit_events SET action='x'", None),
        ("job without project or workspace", "INSERT INTO jobs (id, kind, status, stage, progress, message, payload, result, cancel_requested, attempts, created_by, created_at, updated_at) VALUES ('jx','inspection_analysis','queued','queued',0,'','{}','{}',false,0,'{u1}',{NOW},{NOW})", "ck_jobs_scope"),
        ("unknown job kind", "INSERT INTO jobs (id, workspace_id, kind, status, stage, progress, message, payload, result, cancel_requested, attempts, created_by, created_at, updated_at) VALUES ('jx','{ws}','mine_bitcoin','queued','queued',0,'','{}','{}',false,0,'{u1}',{NOW},{NOW})", "ck_jobs_kind"),
        ("detection model version without thresholds", "UPDATE model_versions SET task='detection' WHERE id='{mv1}'", "ck_mv_detection"),
        ("detection evaluation on the validation split", "INSERT INTO evaluation_runs (id, model_version_id, dataset_version_id, split, n_samples, metrics, per_class, confusion, created_at, task, dataset_checksum, split_digest, iou_threshold, score_threshold) VALUES ('ev1','{mv1}','{dv}','val',10,'{}','[]','[]',{NOW},'detection','{H}','{H}',0.5,0.25)", "ck_eval_detection"),
        ("evaluation targets recorded after the run", "INSERT INTO evaluation_runs (id, model_version_id, dataset_version_id, split, n_samples, metrics, per_class, confusion, created_at, targets, targets_recorded_at) VALUES ('ev2','{mv1}','{dv}','test',10,'{}','[]','[]','2026-10-01T00:00:00+00:00','{}','2026-10-02T00:00:00+00:00')", "ck_eval_targets_before"),
        ("detection dataset version without split strategy", "UPDATE dataset_versions SET task='detection' WHERE id='{dv}'", "ck_dv_detection"),
    ]
    pg_ok = [
        ("integrity flag may change", "UPDATE inspection_media SET integrity='checksum_mismatch', integrity_checked_at={NOW} WHERE id='m1'"),
        ("retention may purge an available report", "UPDATE inspection_reports SET purged_at={NOW} WHERE id='rep1'"),
        ("workspace-scoped inspection job", "INSERT INTO jobs (id, workspace_id, subject_type, subject_id, kind, status, stage, progress, message, payload, result, cancel_requested, attempts, created_by, created_at, updated_at) VALUES ('jy','{ws}','inspection','i2','inspection_analysis','queued','queued',0,'','{}','{}',false,0,'{u1}',{NOW},{NOW})"),
        ("existing classification job still valid", "INSERT INTO jobs (id, project_id, kind, status, stage, progress, message, payload, result, cancel_requested, attempts, created_by, created_at, updated_at) VALUES ('jz','{p}','train','queued','queued',0,'','{}','{}',false,0,'{u1}',{NOW},{NOW})"),
        ("complete detection model metadata", "UPDATE model_versions SET task='detection', taxonomy_version='TAX-1.0', score_threshold=0.25, iou_threshold=0.5, review_threshold=0.5, input_width=640, input_height=640, artifact_format='onnx' WHERE id='{mv2}'"),
    ]
    raw = engine.raw_connection()
    cur = raw.cursor()
    results, failures = [], []

    def run(label, sql, expect_ok, want=None, scalar=None):
        cur.execute("SAVEPOINT sp")
        try:
            cur.execute(q(sql))
            got = cur.fetchone()[0] if sql.lstrip().upper().startswith("SELECT") else None
            ok = expect_ok and (scalar is None or got == scalar)
            cur.execute("RELEASE SAVEPOINT sp")
            detail = f"returned {got}" if scalar is not None else "accepted"
        except Exception as e:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            msg = str(e).splitlines()[0][:160]
            ok = (not expect_ok) and (want is None or want in str(e))
            detail = "rejected: " + msg
        results.append((("PASS" if ok else "FAIL"), label, detail))
        if not ok:
            failures.append(label)

    try:
        if not pg:
            cur.execute("PRAGMA foreign_keys=ON")
        for s in setup:
            cur.execute(q(s))
        for label, sql, scalar in ok_cases:
            run(label, sql, True, scalar=scalar)
        for label, sql in bad_cases:
            run(label, sql, False)
        if pg:
            for label, sql, want in pg_cases:
                run(label, sql, False, want=want or "VT_IMMUTABLE")
            for label, sql in pg_ok:
                run(label, sql, True)
        raw.rollback()
    finally:
        raw.close()
    for r in results:
        print(f"  {r[0]}  {r[1]} — {r[2]}")
    print(f"{engine.dialect.name}: {len(results) - len(failures)}/{len(results)} constraint cases behaved as designed")
    return 1 if failures else 0


def main() -> int:
    if "--_sql-child" in sys.argv:
        return sql_child()
    cat = json.loads(read(CATALOG))
    ddl = parse_ddl()
    write = "--write" in sys.argv
    problems = static_checks(cat, ddl) + apply_generated(write, cat, ddl)
    n_tables = len(ddl["tables"])
    print(f"catalog {cat['version']}: {len(cat['components'])} components, {len(cat['tables'])} tables ({n_tables} new in DDL), "
          f"{len(cat['state_machines'])} state machines, {len(cat['endpoints'])} endpoints, {len(cat['error_codes'])} error codes, "
          f"{len(cat['requirements'])} requirements mapped")
    for p in problems:
        print("PROBLEM:", p)
    print(f"static checks: {len(problems)} problem(s)")
    code = 1 if problems else 0
    if "--sql" in sys.argv:
        code = max(code, sql_mode(sys.argv[sys.argv.index("--sql") + 1]))
    return code


if __name__ == "__main__":
    sys.exit(main())
