"""Phase 12 API latency benchmark against a running VisionTra server (development data only).

Seeds N saved inspections (with reviewer findings and decisions on a share of them) through the public API,
then times the endpoints the inspection pages call. Prints JSON: p50/p95/max per endpoint (ms), sample size.

    python scripts/perf/api_bench.py --base http://127.0.0.1:8820 --n 200 --repeat 30
Run only against a throw-away development server: it creates an account and records.
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import time
import uuid

import httpx
from PIL import Image


def jpeg(seed: int) -> bytes:
    img = Image.new("RGB", (640, 480), (60 + seed % 150, 90, 110))
    for i in range(0, 640, 17):
        img.putpixel((i, (i * (seed + 7)) % 480), (255, 255, 255))
    b = io.BytesIO(); img.save(b, "JPEG"); return b.getvalue()


def post(s, url, **kw):
    """POST, waiting out the per-user review rate limit (429 + Retry-After) instead of failing the seed."""
    while True:
        r = s.post(url, **kw)
        if r.status_code != 429:
            r.raise_for_status(); return r
        time.sleep(float(r.headers.get("Retry-After", "5")))


def pct(xs, p):
    xs = sorted(xs); k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))); return xs[k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8820"); ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--repeat", type=int, default=30); ap.add_argument("--email"); ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args()
    s = httpx.Client(base_url=a.base, timeout=120)
    email = a.email or f"bench-{uuid.uuid4().hex[:8]}@example.org"
    r = s.post("/api/auth/register", json={"name": "Bench", "email": email, "password": "visentra-bench-2026"}) if not a.email else None
    if r is None or r.status_code >= 300:
        r = s.post("/api/auth/login", json={"email": email, "password": "visentra-bench-2026"}); r.raise_for_status()
    h = {"X-CSRF-Token": s.cookies["visentra_csrf"]}
    ids = []
    if not a.no_seed:
        for k in range(a.n):
            r = s.post("/api/v1/inspections", headers=h, files={"file": ("p.jpg", jpeg(k + 1), "image/jpeg")}, data={"component": "fuselage", "analyse": "false"})
            r.raise_for_status(); ins = r.json(); ids.append(ins["id"])
            if k % 2 == 0:                                   # half get a reviewer finding; a quarter are signed off
                f = post(s, f"/api/v1/inspections/{ins['id']}/findings", headers={**h, "If-Match": '"0"'},
                         json={"media_id": ins["media"][0]["id"], "category": "dent" if k % 4 else "crack", "box": {"x": .2, "y": .2, "w": .1, "h": .1}})
                if k % 4 == 0:
                    post(s, f"/api/v1/inspections/{ins['id']}/signoff", headers={**h, "If-Match": f'"{f.json()["revision"]}"'}, json={})
    if a.seed_only:
        print(json.dumps({"email": email, "seeded": len(ids)})); return
    first = s.get("/api/v1/inspections?per_page=1").json()["items"][0]["id"]
    targets = {
        "list page 1 (25)": "/api/v1/inspections?per_page=25",
        "list page 1 (100)": "/api/v1/inspections?per_page=100",
        "list filtered by category=dent": "/api/v1/inspections?category=dent&per_page=25",
        "list status=in_review": "/api/v1/inspections?status=in_review&per_page=25",
        "get one inspection": f"/api/v1/inspections/{first}",
        "review history": f"/api/v1/inspections/{first}/decisions",
        "capabilities": "/api/system/capabilities",
    }
    extra = [("inspection analytics", "/api/v1/inspection-analytics"), ("detection datasets", "/api/v1/detection-datasets"),
             ("dataset image list (48)", "/api/v1/detection-dataset-versions/VT-PREP-001/images?dataset=VT-DS-001&split=train&per_page=48"), ("dataset image list, class filter", "/api/v1/detection-dataset-versions/VT-PREP-001/images?source_class=crack&per_page=48")]
    for name, url in extra:
        if s.get(url).status_code == 200:
            targets[name] = url
    out = {"base": a.base, "repeat": a.repeat, "results": {}}
    for name, url in targets.items():
        s.get(url)                                           # warm-up
        t = []
        for _ in range(a.repeat):
            t0 = time.perf_counter(); r = s.get(url); t.append((time.perf_counter() - t0) * 1000); r.raise_for_status()
        out["results"][name] = {"p50": round(statistics.median(t), 1), "p95": round(pct(t, 95), 1), "max": round(max(t), 1), "bytes": len(r.content)}
    out["total_inspections"] = s.get("/api/v1/inspections?per_page=1").json()["total"]
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
