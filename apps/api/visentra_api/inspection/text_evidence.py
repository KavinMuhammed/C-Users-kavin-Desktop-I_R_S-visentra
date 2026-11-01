"""Authorised persistence and human validation of OCR evidence."""
import hashlib

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from ..audit import audit
from ..errors import api_error
from ..models import Inspection, InspectionMedia, TextEvidence, now
from ..storage import storage
from . import ocr, service as S
from .media import sanitize_filename


def out(row):
    return {k: getattr(row, k) for k in (
        "id", "inspection_id", "filename", "document_revision", "sha256", "mime", "status",
        "engine", "pages", "original_text", "corrected_text", "confirmed_by", "error_code", "revision"
    )} | {"created_at": S.iso(row.created_at), "confirmed_at": S.iso(row.confirmed_at),
         "source_url": f"/api/v1/inspections/{row.inspection_id}/text-evidence/{row.id}/source"}


def list_evidence(db, user, iid, query=""):
    S.inspection_for_user(db, iid, user)
    stmt = select(TextEvidence).where(TextEvidence.inspection_id == iid)
    if query:
        stmt = stmt.where(or_(TextEvidence.original_text.icontains(query, autoescape=True),
                             TextEvidence.corrected_text.icontains(query, autoescape=True)))
    return {"items": [out(r) for r in db.scalars(stmt.order_by(TextEvidence.created_at))]}


def _editable(db, user, iid):
    insp, _ = S.inspection_for_user(db, iid, user, "inspection.review")
    if insp.status == "reviewed":
        raise api_error(409, "CONFLICT", "Reopen the inspection before changing text evidence.")
    return insp


def _lock_editable(db, user, iid):
    """Serialize evidence writes with the inspection's optimistic sign-off lock."""
    db.commit()  # End the SQLite read snapshot before starting a write.
    db.expire_all()
    insp = _editable(db, user, iid)
    expected = insp.revision
    changed = db.execute(update(Inspection).where(
        Inspection.id == iid, Inspection.revision == expected,
        Inspection.status != "reviewed", Inspection.deleted_at.is_(None)
    ).values(revision=expected + 1, updated_at=now()))
    if changed.rowcount != 1:
        db.rollback()
        raise api_error(409, "CONFLICT", "Inspection changed. Reload before saving text evidence.")
    return insp


def create(db, user, iid, data=None, filename=None, revision=None):
    insp = _editable(db, user, iid)
    if revision and len(revision) > 120:
        raise api_error(422, "VALIDATION", "Document revision is limited to 120 characters.")
    if data is None:
        media = db.scalar(select(InspectionMedia).where(InspectionMedia.inspection_id == iid).order_by(InspectionMedia.ordinal))
        if media is None:
            raise api_error(404, "NOT_FOUND", "No source image was found.")
        try:
            data = storage.get(media.object_key)
        except OSError:
            raise api_error(503, "STORAGE_UNAVAILABLE", "The source image cannot be read.") from None
        filename = media.filename
        if hashlib.sha256(data).hexdigest() != media.sha256:
            raise api_error(409, "INTEGRITY_FAILED", "The source image checksum does not match.")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(TextEvidence).where(TextEvidence.inspection_id == iid, TextEvidence.sha256 == digest))
    if existing and existing.status != "failed":
        return out(existing)
    existing_revision = existing.revision if existing else None
    db.commit()  # OCR must not hold an open database read snapshot.
    try:
        mime = ocr.validate(data)
    except ocr.ExtractionError as ex:
        raise api_error(503 if ex.code == "OCR_UNAVAILABLE" else 422, ex.code, str(ex)) from None
    try:
        result = ocr.extract(data, mime)
        error = None
    except ocr.ExtractionError as ex:
        result = {"pages": [], "original_text": "", "status": "failed", "engine": "Local text extraction"}
        error = ex.code
    insp = _lock_editable(db, user, iid)
    # A concurrent retry may have finished while this request was extracting.
    existing = db.scalar(select(TextEvidence).where(TextEvidence.inspection_id == iid, TextEvidence.sha256 == digest))
    if existing and (existing.status != "failed" or existing.revision != existing_revision):
        db.rollback()
        return out(existing)
    row = existing or TextEvidence(inspection_id=iid, workspace_id=insp.workspace_id,
                                  sha256=digest, created_by=user.id, revision=1)
    key = f"text-evidence/{iid}/{digest}.bin"
    try:
        storage.put(key, data, mime)
    except OSError:
        raise api_error(503, "STORAGE_UNAVAILABLE", "The source document could not be saved.") from None
    row.filename = sanitize_filename(filename or "source")
    row.document_revision, row.mime, row.object_key = revision, mime, key
    for k, v in result.items():
        setattr(row, k, v)
    row.error_code = error
    if existing:
        row.revision += 1
    db.add(row)
    try:
        db.flush()
        audit(db, "text.extract", "inspection", iid, user_id=user.id,
              result="failure" if error else "success",
              meta={"workspace_id": insp.workspace_id, "evidence_id": row.id, "sha256": digest, "status": row.status})
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(TextEvidence).where(TextEvidence.inspection_id == iid, TextEvidence.sha256 == digest))
        if row is None:
            raise
    return out(row)


def get_row(db, user, iid, eid, permission="inspection.read"):
    insp, _ = S.inspection_for_user(db, iid, user, permission)
    row = db.get(TextEvidence, eid)
    if row is None or row.inspection_id != iid or row.workspace_id != insp.workspace_id:
        raise api_error(404, "NOT_FOUND", "Text evidence was not found.")
    return row


def correct(db, request, user, iid, eid, body):
    _editable(db, user, iid)
    row = get_row(db, user, iid, eid)
    expected = S.parse_if_match(request)
    text = body.get("text")
    if not isinstance(text, str) or len(text) > ocr.MAX_TEXT or body.get("confirmed") is not True:
        raise api_error(422, "VALIDATION", "Supply text and confirm that you checked it against the source.")
    if row.status == "failed":
        raise api_error(409, "CONFLICT", "Retry failed extraction before confirming text.")
    _lock_editable(db, user, iid)
    db.refresh(row)
    before = row.corrected_text
    updated = db.execute(update(TextEvidence).where(TextEvidence.id == eid, TextEvidence.revision == expected).values(
        corrected_text=text, confirmed_by=user.id, confirmed_at=now(), revision=expected + 1))
    if updated.rowcount != 1:
        db.rollback()
        raise api_error(409, "CONFLICT", "Text evidence changed. Reload before saving.")
    audit(db, "text.confirm", "inspection", iid, user_id=user.id,
          meta={"workspace_id": row.workspace_id, "evidence_id": eid, "previous_text": before, "corrected_text": text})
    db.commit()
    db.refresh(row)
    return out(row)
