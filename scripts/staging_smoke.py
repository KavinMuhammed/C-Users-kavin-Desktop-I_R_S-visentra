"""Staging smoke test: checks a DEPLOYED VisionTra over HTTPS, as a client would (docs/STAGING_PLAN.md §5).

    python scripts/staging_smoke.py --base https://staging.example.org --accounts <accounts.json> \
        --manifest <RELEASE_MANIFEST.json from the bundle> [--cacert <caddy root.crt>] [--out result.json]

accounts.json (keep it outside the project; two operator-created accounts in DIFFERENT workspaces):
    {"a": {"email": "...", "password": "..."}, "b": {"email": "...", "password": "..."}}

It creates one inspection named "STAGING SMOKE <time>" for account A (upload, real detection, one confirm + bulk reject,
sign-off, report) and leaves it on staging as evidence; nothing else is written. Passwords are never printed or saved.
Exit code 0 only when every check passes. --allow-http is for the local dry run only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = ROOT / "datasets/demo/VT-DEMO-001/images/dent_VT-DS-001_PG-00046.jpg"
results: list[dict] = []


def check(name: str, ok: bool, **detail) -> bool:
    results.append({"check": name, "ok": bool(ok), **detail})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {json.dumps(detail, default=str)[:300]}"))
    return bool(ok)


class Client:
    def __init__(self, base: str, verify):
        self.c = httpx.Client(base_url=base, verify=verify, timeout=180, follow_redirects=False)
        self.csrf = None

    def login(self, acct: dict) -> httpx.Response:
        r = self.c.post("/api/auth/login", json={"email": acct["email"], "password": acct["password"]})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def req(self, method: str, url: str, **kw) -> httpx.Response:
        h = kw.pop("headers", {})
        if method != "GET" and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return self.c.request(method, url, headers=h, **kw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--accounts", required=True)
    ap.add_argument("--manifest", required=True, help="RELEASE_MANIFEST.json of the bundle that was deployed")
    ap.add_argument("--image", default=str(DEFAULT_IMAGE))
    ap.add_argument("--cacert", help="CA certificate for a private (tls internal) staging certificate")
    ap.add_argument("--allow-http", action="store_true", help="local dry run without TLS (skips the HTTPS/HSTS/Secure checks)")
    ap.add_argument("--out")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    accts = json.loads(Path(a.accounts).read_text(encoding="utf-8"))
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    https = base.startswith("https://")
    if not https and not a.allow_http:
        print("--base must be https:// (use --allow-http only for the local dry run)", file=sys.stderr); return 2
    verify = a.cacert or True
    A, B = Client(base, verify), Client(base, verify)
    started = datetime.now(timezone.utc)

    # 1. transport, liveness, readiness, version
    r = A.c.get("/api/health/live")
    check("liveness", r.status_code == 200 and r.json().get("status") == "ok", status=r.status_code)
    if https:
        check("HSTS header", "max-age=" in r.headers.get("strict-transport-security", ""), hsts=r.headers.get("strict-transport-security"))
        try:
            plain = httpx.get(base.replace("https://", "http://", 1) + "/api/health/live", timeout=15, follow_redirects=False)
            check("plain HTTP redirects to HTTPS", plain.status_code in (301, 302, 307, 308) and plain.headers.get("location", "").startswith("https://"),
                  status=plain.status_code)
        except httpx.HTTPError as ex:
            check("plain HTTP redirects to HTTPS", True, note=f"port 80 not reachable ({type(ex).__name__}); only HTTPS is exposed")
    check("no Server header", "server" not in {k.lower() for k in r.headers}, server=r.headers.get("server"))
    ready = A.c.get("/api/health/ready")
    rj = ready.json()
    check("readiness: all checks ok", ready.status_code == 200 and rj.get("status") == "ok", checks={k: v.get("status") for k, v in rj.get("checks", {}).items()})
    ver = A.c.get("/api/system/version").json()
    check("production environment", ver.get("environment") == "production", environment=ver.get("environment"))
    check("frontend build matches the bundle", ver.get("frontend_build") == man["frontend_build"], deployed=ver.get("frontend_build"), bundle=man["frontend_build"])

    # 2. hardening visible from outside
    for path in ("/api/docs", "/api/openapi.json"):
        check(f"{path} disabled", A.c.get(path).status_code == 404)
    page = A.c.get("/")
    check("UI served with security headers", page.status_code == 200 and "default-src 'self'" in page.headers.get("content-security-policy", "")
          and page.headers.get("x-frame-options") == "DENY" and page.headers.get("x-content-type-options") == "nosniff", status=page.status_code)
    reg = A.c.post("/api/auth/register", json={"name": "Smoke", "email": f"smoke-{int(time.time())}@example.org", "password": "correct horse 42"})
    check("self-registration closed", reg.status_code == 403, status=reg.status_code)
    check("anonymous API refused", A.c.get("/api/inspections").status_code == 401)

    # 3. sign-in and session cookies
    la = A.login(accts["a"])
    if not check("sign in A", la.status_code == 200, status=la.status_code):
        return finish(a, base, started, man)
    sc = [v for k, v in la.headers.multi_items() if k.lower() == "set-cookie"]
    sess = next((c for c in sc if c.startswith("visentra_session=")), "")
    check("session cookie HttpOnly" + (" + Secure" if https else ""), "httponly" in sess.lower() and ("secure" in sess.lower() or not https),
          flags=[p.strip() for p in sess.split(";")[1:]])
    check("bad password refused", Client(base, verify).login({"email": accts["a"]["email"], "password": "wrong-password-0"}).status_code == 401)

    # 4. model and gate on the server are the ones in the bundle
    dm = A.req("GET", "/api/detection-model").json()
    gate = dm.get("domain_gate") or {}
    check("detector is the bundled model", dm.get("model_id") == man["models"]["detection"] and dm.get("weights_sha256") == man["models"]["detection_weights_sha256"],
          model=dm.get("model_id"), sha=dm.get("weights_sha256"))
    check("gate is the bundled gate", gate.get("gate_id") == man["models"]["gate"], gate=gate.get("gate_id"), state=gate.get("state"))

    # 5. upload → real detection → review → persistence → sign-off → report
    img = Path(a.image).read_bytes()
    label = "STAGING SMOKE " + started.strftime("%Y-%m-%d %H:%M:%SZ")
    total0 = A.req("GET", "/api/inspections").json()["total"]
    t0 = time.perf_counter()
    up = A.req("POST", "/api/inspections", files={"file": (Path(a.image).name, img, "image/jpeg")}, data={"component": "fuselage", "notes": label})
    body = up.json() if up.headers.get("content-type", "").startswith("application/json") else {}
    preds = body.get("predictions", [])
    ok = check("upload analysed by the real detector", up.status_code == 201 and body.get("analysis_mode") == "model" and (body.get("analysis") or {}).get("model_id") == man["models"]["detection"],
               status=up.status_code, mode=body.get("analysis_mode"), state=body.get("status"), predictions=len(preds), seconds=round(time.perf_counter() - t0, 1))
    if not ok:
        return finish(a, base, started, man)
    iid, media = body["id"], body["media"][0]
    orig = A.req("GET", media["urls"]["original"])
    check("original stored byte-for-byte", orig.status_code == 200 and hashlib.sha256(orig.content).hexdigest() == hashlib.sha256(img).hexdigest() == media["sha256"],
          status=orig.status_code)
    check("thumbnail served", A.req("GET", media["urls"]["thumb"]).status_code == 200)
    bad = A.req("POST", "/api/inspections", files={"file": ("fake.jpg", b"not an image", "image/jpeg")}, data={"component": "fuselage"})
    check("non-image refused, nothing saved", bad.status_code == 415 and A.req("GET", "/api/inspections").json()["total"] == total0 + 1, status=bad.status_code)
    if len(preds) >= 1:
        rev = body["revision"]
        d1 = A.req("POST", f"/api/inspections/{iid}/decisions", headers={"If-Match": f'"{rev}"'},
                   json={"target": {"type": "prediction", "id": preds[0]["id"]}, "decision": "confirmed"})
        check("confirm one proposal", d1.status_code == 200, status=d1.status_code)
        rev = d1.json().get("revision", rev)
        rest = [p["id"] for p in preds[1:]]
        if rest:
            d2 = A.req("POST", f"/api/inspections/{iid}/decisions/bulk", headers={"If-Match": f'"{rev}"'}, json={"decision": "rejected", "targets": rest})
            check("reject the rest in one request", d2.status_code == 200, status=d2.status_code)
    cur = A.req("GET", f"/api/inspections/{iid}").json()
    decs = [v.get("decision") for v in cur.get("current_decisions", {}).values()]
    check("decisions persisted", "pending" not in decs and decs.count("confirmed") == min(1, len(preds)), decisions=decs)
    stale = A.req("POST", f"/api/inspections/{iid}/signoff", headers={"If-Match": f'"{cur["revision"] - 1}"'}, json={"note": label})
    check("stale sign-off refused", stale.status_code == 409, status=stale.status_code)
    so = A.req("POST", f"/api/inspections/{iid}/signoff", headers={"If-Match": f'"{cur["revision"]}"'},
               json={"note": label, **({"acknowledgement": cur["signoff_requires"]} if cur.get("signoff_requires") else {})})
    check("sign-off", so.status_code == 200, status=so.status_code)
    rp = A.req("POST", f"/api/inspections/{iid}/reports", json={})
    rep = rp.json() if rp.status_code in (200, 201, 202) else {}
    for _ in range(120):
        if rep.get("status") in ("available", "failed") or not rep.get("id"):
            break
        time.sleep(1)
        rep = A.req("GET", f"/api/reports/{rep['id']}").json()
    dl = A.req("GET", f"/api/reports/{rep['id']}/download") if rep.get("id") else None
    check("PDF report generated and downloadable", bool(dl) and dl.status_code == 200 and dl.content[:5] == b"%PDF-", status=rep.get("status"),
          bytes=len(dl.content) if dl else 0)
    hist = A.req("GET", "/api/inspections", params={"q": cur["ref"]}).json()
    check("history search finds it", any(x["id"] == iid for x in hist.get("items", [])), ref=cur["ref"])

    # 6. isolation: account B (another workspace) sees none of it
    if check("sign in B", B.login(accts["b"]).status_code == 200):
        got = {k: B.req("GET", u).status_code for k, u in (("inspection", f"/api/inspections/{iid}"), ("original", media["urls"]["original"]),
                                                            ("report", f"/api/reports/{rep.get('id')}/download"))}
        check("other workspace gets 404 for inspection, image and report", all(s == 404 for s in got.values()), statuses=got)
        check("other workspace's list does not contain it", all(x["id"] != iid for x in B.req("GET", "/api/inspections").json()["items"]))

    # 7. logout ends the session
    A.req("POST", "/api/auth/logout")
    check("logout ends the session", A.c.get("/api/auth/me").status_code == 401)
    return finish(a, base, started, man, inspection={"id": iid, "ref": cur.get("ref"), "label": label})


def finish(a, base, started, man, **extra) -> int:
    failed = [r["check"] for r in results if not r["ok"]]
    out = {"base": base, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(), "bundle_build": man["frontend_build"],
           "passed": len(results) - len(failed), "failed": failed, "results": results, **extra}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
