"""Test fixtures. Images used in tests are generated synthetically *inside the tests* only;
the application itself never generates or preloads data."""
from __future__ import annotations

import io
import os
import random
import shutil
import tempfile
import zipfile

_TMP = tempfile.mkdtemp(prefix="visentra-test-")
os.environ.update({
    "VISENTRA_ENV": "test", "VISENTRA_DATA_DIR": _TMP, "WORKER_MODE": "external", "JWT_SECRET": "test-secret-" + "x" * 40,
    "ALLOWED_SOURCE_HOSTS": "127.0.0.1,localhost,example.org", "LOG_LEVEL": "WARNING", "TORCH_THREADS": "2",
    # Phase 13: automatic analysis is off for the general suite (tests that need it install a fixture bundle)
    "VISIONTRA_INSPECTION_MODEL": "off", "VISIONTRA_DOMAIN_GATE": "off",
})
# Run the whole suite against PostgreSQL with TEST_DATABASE_URL=postgresql+psycopg://... (SQLite by default)
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from visentra_api.main import app, migrate  # noqa: E402
from visentra_api.security import limiter  # noqa: E402

migrate()


def make_image(kind: str, seed: int, size: int = 96, fmt: str = "PNG") -> bytes:
    """Two visually distinct synthetic classes: 'scratch' (bright diagonal strokes) vs 'clean' (smooth gradient)."""
    r = random.Random(seed)
    base = r.randint(90, 140)
    img = Image.new("RGB", (size, size), (base, base, base + 5))
    d = ImageDraw.Draw(img)
    for y in range(size):
        v = base + int(20 * y / size) + r.randint(-6, 6)
        d.line([(0, y), (size, y)], fill=(v, v, v + 4))
    for _ in range(6):  # low-contrast texture so images are not near-duplicates of each other
        x, y = r.randint(0, size), r.randint(0, size)
        t = base + r.randint(-35, 35)
        d.rectangle([x, y, x + r.randint(8, 30), y + r.randint(8, 30)], fill=(t, t, t))
    if kind == "scratch":
        for _ in range(r.randint(2, 4)):
            x0, y0 = r.randint(0, size // 2), r.randint(0, size)
            d.line([(x0, y0), (x0 + r.randint(size // 3, size), y0 + r.randint(-size // 2, size // 2))], fill=(250, 250, 250), width=r.randint(2, 4))
    elif kind == "dent":
        cx, cy, rad = r.randint(25, size - 25), r.randint(25, size - 25), r.randint(10, 20)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(40, 40, 45))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def make_zip(per_class: int = 24, classes=("scratch", "clean"), seed0: int = 0, extras: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ci, c in enumerate(classes):
            for i in range(per_class):
                z.writestr(f"dataset/train/{c}/{c}_{i}.png", make_image(c, seed0 + ci * 1000 + i))
        if extras:
            z.writestr("dataset/train/scratch/broken.png", b"\x89PNG\r\n\x1a\nnot really a png")
            z.writestr("dataset/train/scratch/dup.png", make_image("scratch", seed0))  # exact duplicate of scratch_0
            z.writestr("../../evil/escape.png", make_image("clean", 99999))  # zip-slip style name: must be harmless
            z.writestr("notes.txt", b"ignored")
    return buf.getvalue()


class Api:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.csrf = None

    def _h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)

    def register(self, email, name="Test User", password="correct horse 42"):
        r = self.c.post("/api/auth/register", json={"name": name, "email": email, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()


@pytest.fixture()
def api():
    limiter.reset()
    with TestClient(app) as c:
        yield Api(c)


@pytest.fixture()
def api2():
    with TestClient(app) as c:
        yield Api(c)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
