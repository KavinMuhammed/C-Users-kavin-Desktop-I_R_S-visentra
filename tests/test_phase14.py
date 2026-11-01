"""Phase 14: frontend serving and the page/server build handshake that diagnoses a stale server process."""
import json

import pytest

from visentra_api import main as M


@pytest.fixture()
def built():
    if not (M.WEB_DIST / "index.html").is_file() or not (M.WEB_DIST / "vendor" / "aircraft_scene.mjs").is_file():
        pytest.skip("frontend not built (python apps/web/build.py)")
    return json.loads((M.WEB_DIST / "build.json").read_text())


def test_version_reports_frontend_build_and_start_time(api):
    v = api.get("/api/system/version").json()
    assert "frontend_build" in v and "started_at" in v
    assert v["frontend_build"] == M.FRONTEND_BUILD_AT_START
    assert v["started_at"].endswith("Z")


def test_index_carries_build_id_matching_server(api, built):
    r = api.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert f'window.VT_BUILD = "{built["build"]}"' in r.text or f'window.VT_BUILD="{built["build"]}"' in r.text
    assert '"three": "/vendor/three.module.min.js' in r.text or '"three":"/vendor/three.module.min.js' in r.text


@pytest.mark.parametrize("path", ["vendor/aircraft_scene.mjs", "vendor/three.module.min.js", "vendor/addons/controls/OrbitControls.js",
                                  "vendor/addons/environments/RoomEnvironment.js"])
def test_vendor_modules_served_as_javascript_with_gzip_and_etag(api, built, path):
    r = api.get("/" + path, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers.get("etag")
    assert r.headers.get("content-encoding") == "gzip"
    again = api.get("/" + path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_missing_vendor_file_is_404_not_index(api, built):
    r = api.get("/vendor/nope.js")
    assert r.status_code == 404 and "text/html" not in r.headers.get("content-type", "")


def test_path_traversal_outside_dist_is_not_served(api, built):
    r = api.get("/vendor/..%2F..%2F..%2Fpyproject.toml")
    assert r.status_code in (200, 404)
    assert "[project]" not in r.text


def test_three_license_vendored(built):
    lic = (M.WEB_DIST.parent / "vendor" / "three" / "LICENSE").read_text()
    assert "MIT" in lic
