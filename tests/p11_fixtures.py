"""Phase 11 test helpers.

`add_fixture_predictions` inserts AI-prediction rows DIRECTLY into the database for tests only. No operational detection model
exists (VT-GATE-OP-01), so no product code path creates defect_predictions; these rows exist to test the review and report code
paths for predictions. They are named "TEST FIXTURE" and are never presented as real model output.
"""
from __future__ import annotations

import io
import uuid

from PIL import Image

from conftest import Api
from visentra_api.db import SessionLocal
from visentra_api.models import (AnalysisRun, Dataset, DatasetVersion, DefectPrediction, Experiment, Inspection, InspectionMedia, Job, ModelVersion, Project,
                                 now, uid)

URL = "/api/v1/inspections"


def jpeg(w=800, h=600, seed=1) -> bytes:
    img = Image.new("RGB", (w, h), (80 + seed % 120, 100, 120))
    for i in range(0, w, 41):
        img.putpixel((i % w, (i * (seed + 3)) % h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def user(api: Api, prefix="p11") -> dict:
    return api.register(f"{prefix}-{uuid.uuid4().hex[:10]}@example.org")


def create(api: Api, data: bytes | None = None, **fields) -> dict:
    form = {"component": "fuselage", **{k: v for k, v in fields.items() if v is not None}}
    r = api.post(URL, files={"file": ("panel.jpg", data if data is not None else jpeg(seed=uuid.uuid4().int % 997), "image/jpeg")}, data=form)
    assert r.status_code == 201, r.text
    return r.json()


def rev(api: Api, iid: str) -> int:
    return api.get(f"{URL}/{iid}").json()["revision"]


def post(api: Api, iid: str, path: str, body: dict, revision: int | None = None, key: str | None = None):
    h = {}
    if revision is not None:
        h["If-Match"] = f'"{revision}"'
    if key:
        h["Idempotency-Key"] = key
    return api.post(f"{URL}/{iid}/{path}", json=body, headers=h)


def finding(api: Api, insp: dict, category="dent", box=None, comment=None):
    body = {"media_id": insp["media"][0]["id"], "category": category, "box": box or {"x": 0.2, "y": 0.3, "w": 0.1, "h": 0.08}}
    if comment is not None:
        body["comment"] = comment
    return post(api, insp["id"], "findings", body, rev(api, insp["id"]))


def decide(api: Api, iid: str, ttype: str, tid: str, decision: str | None = "confirmed", kind="decision", revision=None, **extra):
    body = {"target": {"type": ttype, "id": tid}, "kind": kind, **extra}
    if decision is not None:
        body["decision"] = decision
    return post(api, iid, "decisions", body, rev(api, iid) if revision is None else revision)


def signoff(api: Api, iid: str, note=None, ack="auto"):
    """ack="auto" passes the acknowledgement the server says this inspection needs (Phase 15: manual-only / visual check);
    ack=None sends none, any other string is sent as given."""
    body = {"note": note} if note else {}
    if ack == "auto":
        g = api.get(f"{URL}/{iid}")
        ack = g.json().get("signoff_requires") if g.status_code == 200 else None
    if ack:
        body["acknowledgement"] = ack
    return post(api, iid, "signoff", body, rev(api, iid))


def add_fixture_predictions(insp_id: str, specs: list[tuple[str, float, tuple[float, float, float, float]]]) -> list[str]:
    """specs: [(class_id, score, (x, y, w, h))]. Returns prediction ids. TEST FIXTURE ONLY (see module docstring)."""
    with SessionLocal() as db:
        insp = db.get(Inspection, insp_id)
        m = db.query(InspectionMedia).filter_by(inspection_id=insp_id).one()
        p = Project(name=f"TEST FIXTURE detector {uuid.uuid4().hex[:6]}", owner_id=insp.created_by, workspace_id=insp.workspace_id, project_type="object_detection")
        db.add(p); db.flush()
        ds = Dataset(project_id=p.id, name="fixture"); db.add(ds); db.flush()
        dv = DatasetVersion(dataset_id=ds.id, project_id=p.id, version=1, seed=1, val_ratio=.15, test_ratio=.15, created_by=insp.created_by); db.add(dv); db.flush()
        job = Job(project_id=p.id, kind="train", created_by=insp.created_by); db.add(job); db.flush()
        ex = Experiment(project_id=p.id, job_id=job.id, dataset_version_id=dv.id, seed=1, created_by=insp.created_by); db.add(ex); db.flush()
        mv = ModelVersion(project_id=p.id, experiment_id=ex.id, dataset_version_id=dv.id, version=3, architecture="TEST-FIXTURE-DET", seed=1, status="COMPLETED")
        db.add(mv); db.flush()
        run = AnalysisRun(id=uid(), inspection_id=insp_id, workspace_id=insp.workspace_id, attempt=1, model_version_id=mv.id, model_checksum="c" * 64,
                          taxonomy_version="TAX-1.0", score_threshold=0.25, iou_threshold=0.5, review_threshold=0.5, status="succeeded",
                          requested_by=insp.created_by, finished_at=now())
        db.add(run); db.flush()
        ids = []
        for k, (cls, score, (x, y, w, h)) in enumerate(specs, 1):
            pid = uid()
            db.add(DefectPrediction(id=pid, inspection_id=insp_id, analysis_run_id=run.id, model_version_id=mv.id, media_id=m.id, ordinal=k, class_id=cls,
                                    model_class_index=0, taxonomy_version="TAX-1.0", score=score, box_x=x, box_y=y, box_w=w, box_h=h,
                                    box_input_xyxy=[1, 2, 3, 4], clipped=False, low_confidence=score < 0.5))
            ids.append(pid)
        db.commit()
        return ids
