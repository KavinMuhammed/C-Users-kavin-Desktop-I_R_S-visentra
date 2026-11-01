"""Detection-dataset API (Phase 12). Read-only views of the repository's dataset registry and prepared version.

    GET /api/v1/detection-datasets                                         EP-20 registry (16 candidate datasets, licences, measured counts)
    GET /api/v1/detection-datasets/{dataset_id}                            registry entry (full provenance)
    GET /api/v1/dataset-sources                                            EP-24 licence register
    GET /api/v1/class-mappings?dataset=                                    EP-25 class mapping CMAP-001
    GET /api/v1/detection-dataset-versions/{version_id}                    EP-21 prepared version (splits, quality, evaluation scope)
    GET /api/v1/detection-dataset-versions/{version_id}/readiness?classes=&datasets=   EP-23 pre-training gate (refuses unresolved classes)
    GET /api/v1/detection-dataset-versions/{version_id}/images?…           EP-39 paged image list with filters (no image bytes)
    GET /api/v1/detection-dataset-versions/{version_id}/images/{image_id}  EP-40 image detail: labels with source classes, mapping status
    GET /api/v1/detection-dataset-versions/{version_id}/images/{image_id}/thumb?size=thumb|preview   EP-41 metadata-free JPEG preview

Any signed-in user with `dataset.read` in a workspace. Nothing is written. Held-out test images are not displayed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import detection_data as DD
from .. import errors as E
from .. import permissions as P
from ..db import get_db
from ..errors import api_error
from ..models import User, WorkspaceMember
from ..security import current_user

router = APIRouter(prefix="/api", tags=["detection-datasets"])


def reader(u: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    roles = db.scalars(select(WorkspaceMember.role).where(WorkspaceMember.user_id == u.id)).all()
    if not any(P.workspace_can(r, "dataset.read") for r in roles):
        raise api_error(403, E.FORBIDDEN, "Your roles do not include reading datasets.")
    return u


@router.get("/detection-datasets")
def list_datasets(u: User = Depends(reader)):
    return DD.list_datasets()


@router.get("/detection-datasets/{dataset_id}")
def get_dataset(dataset_id: str, u: User = Depends(reader)):
    return DD.get_dataset(dataset_id)


@router.get("/dataset-sources")
def dataset_sources(u: User = Depends(reader)):
    return DD.sources()


@router.get("/class-mappings")
def class_mappings(dataset: Optional[str] = Query(None, max_length=32), u: User = Depends(reader)):
    return DD.class_mappings(dataset)


@router.get("/detection-dataset-versions/{version_id}")
def get_version(version_id: str, u: User = Depends(reader)):
    return DD.get_version(version_id)


@router.get("/detection-dataset-versions/{version_id}/readiness")
def readiness(version_id: str, classes: str = Query("dent", max_length=100), datasets: str = Query("VT-DS-001", max_length=100), u: User = Depends(reader)):
    return DD.readiness(version_id, [c for c in classes.split(",") if c], [d for d in datasets.split(",") if d])


@router.get("/detection-dataset-versions/{version_id}/images")
def list_images(version_id: str, page: int = 1, per_page: int = 48, dataset: Optional[str] = Query(None, max_length=32),
                split: Optional[str] = Query(None, max_length=10), source_class: Optional[str] = Query(None, max_length=40),
                mapped_class: Optional[str] = Query(None, max_length=40), annotation_status: Optional[str] = Query(None, max_length=40),
                inclusion_status: Optional[str] = Query(None, max_length=40), flag: Optional[str] = Query(None, max_length=60), u: User = Depends(reader)):
    return DD.list_images(version_id, page, per_page, {"dataset": dataset, "split": split, "source_class": source_class, "mapped_class": mapped_class,
                                                      "annotation_status": annotation_status, "inclusion_status": inclusion_status, "flag": flag})


@router.get("/detection-dataset-versions/{version_id}/images/{image_id}")
def image_detail(version_id: str, image_id: str, verify: bool = False, u: User = Depends(reader)):
    return DD.image_detail(version_id, image_id, verify)


@router.get("/detection-dataset-versions/{version_id}/images/{image_id}/thumb")
def thumb(version_id: str, image_id: str, request: Request, size: str = "thumb", u: User = Depends(reader)):
    data, sha = DD.thumbnail(version_id, image_id, size)
    etag = f'"{sha[:32]}-{DD.SIZES.get(size)}-{DD.THUMB_VERSION}"'
    headers = {"Cache-Control": "private, max-age=86400", "ETag": etag, "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; sandbox", "Cross-Origin-Resource-Policy": "same-origin"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="image/jpeg", headers=headers)


_EXAMPLES: dict = {}


@router.get("/training-examples")
def training_examples(per_class: int = Query(6, ge=1, le=12), u: User = Depends(reader)):
    """Phase 13: real labelled training images per defect class (what the detection model learned from)."""
    key = (per_class, DD._mtimes())
    if key not in _EXAMPLES:
        _EXAMPLES.clear()
        _EXAMPLES[key] = DD.training_examples(per_class)
    return _EXAMPLES[key]
