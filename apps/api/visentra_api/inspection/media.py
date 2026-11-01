"""Inspection media intake: content validation (API-01 policy), capture metadata without GPS, advisory quality
metrics and EXIF-free renditions. Pure functions over bytes; nothing here touches the database or storage.

Policy (Phase 13): any common still-image format decided by content (JPEG, PNG, WebP, BMP, TIFF, GIF, HEIC/HEIF, AVIF),
≤ INSPECTION_MAX_UPLOAD_MB (5 MB), ≤ MAX_IMAGE_PIXELS, ≤ INSPECTION_MAX_SIDE px per side, shorter side ≥ INSPECTION_MIN_SIDE px
(sizes after EXIF orientation). Animated GIFs use their first frame.
The original bytes are stored unchanged (evidence); only renditions are re-encoded.
"""
from __future__ import annotations

import hashlib
import io
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import unicodedata

import numpy as np
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

from .. import errors as E
from ..config import settings

try:                                    # HEIC/HEIF (iPhone photos); optional dependency pillow-heif
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:                       # noqa: BLE001 — without it HEIC files are refused with a clear message
    register_heif_opener = None

FORMATS = {"JPEG": ("image/jpeg", "jpg"), "MPO": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp"),
           "BMP": ("image/bmp", "bmp"), "TIFF": ("image/tiff", "tif"), "GIF": ("image/gif", "gif"), "HEIF": ("image/heic", "heic"),
           "AVIF": ("image/avif", "avif")}
FORMAT_NAMES = "JPEG, PNG, WebP, BMP, TIFF, GIF, HEIC or AVIF"
# Formats kept byte-for-byte as the evidence original. Every other accepted format is converted once, losslessly, to PNG
# (first frame, EXIF orientation applied); the uploaded file's format, size and SHA-256 are recorded in `capture.source`.
STORED_AS_IS = {"JPEG": ("image/jpeg", "jpg"), "MPO": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}


def open_formats() -> list[str]:
    """Pillow openers allowed to read untrusted bytes: only the accepted formats (MPO comes from the JPEG opener; HEIF only
    when pillow-heif registered it). Without this, Image.open tries every plugin Pillow has — EPS, MSP, Spider, XBM … —
    before the format check, exposing their parsers to uploads (pip-audit, 27 Oct 2026)."""
    PILImage.init()
    return [f for f in FORMATS if f in PILImage.OPEN]


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return (name or "image")[:200]


DISPLAY_SIDE = 2048
THUMB_SIDE = 320
_TAG_MAKE, _TAG_MODEL, _TAG_DATETIME, _TAG_DT_ORIGINAL = 0x010F, 0x0110, 0x0132, 0x9003
_IFD_EXIF, _IFD_GPS = 0x8769, 0x8825


class MediaRejected(Exception):
    def __init__(self, status: int, code: str, message: str, reason: str | None = None):
        super().__init__(message)
        self.status, self.code, self.reason = status, code, reason


@dataclass
class Rendition:
    kind: str
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str


@dataclass
class ValidatedMedia:
    fmt: str
    mime: str
    ext: str
    width: int                 # oriented (EXIF applied) — the frame boxes and renditions use
    height: int
    size_bytes: int
    sha256: str
    capture: dict[str, Any]
    quality: dict[str, Any]
    renditions: list[Rendition] = field(default_factory=list)
    stored: bytes | None = None   # bytes kept as the evidence original (the upload itself, or its lossless PNG conversion)


def _clean(v: Any, n: int = 60) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    s = re.sub(r"[^\x20-\x7E]", "", str(v)).strip().strip("\x00")
    return s[:n] or None


def capture_metadata(img: PILImage.Image) -> dict[str, Any]:
    """{taken_at?, camera_make?, camera_model?, has_gps}. GPS coordinates are never read into the result (D-11)."""
    out: dict[str, Any] = {"has_gps": False}
    try:
        ex = img.getexif()
    except Exception:
        return out
    if not ex:
        return out
    try:
        out["has_gps"] = bool(ex.get_ifd(_IFD_GPS))
    except Exception:
        out["has_gps"] = _IFD_GPS in ex
    make, model = _clean(ex.get(_TAG_MAKE)), _clean(ex.get(_TAG_MODEL))
    if make:
        out["camera_make"] = make
    if model:
        out["camera_model"] = model
    raw = None
    try:
        raw = ex.get_ifd(_IFD_EXIF).get(_TAG_DT_ORIGINAL)
    except Exception:
        pass
    raw = _clean(raw or ex.get(_TAG_DATETIME), 19)
    if raw:
        try:
            out["taken_at"] = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").isoformat()   # camera local time, no zone
        except ValueError:
            pass
    return out


def quality_metrics(rgb: PILImage.Image) -> dict[str, Any]:
    """Advisory, server-measured metrics on a ≤ 512 px greyscale copy (FR-QLT-02). Not a verdict."""
    g = rgb.convert("L")
    g.thumbnail((512, 512))
    a = np.asarray(g, dtype=np.float32)
    lap = a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1] if min(a.shape) >= 3 else np.zeros(1)
    return {"mean_luminance": round(float(a.mean()) / 255, 4), "clipped_highlights": round(float((a >= 250).mean()), 4),
            "clipped_shadows": round(float((a <= 5).mean()), 4), "sharpness_laplacian_var": round(float(lap.var()), 2),
            "measured_on": f"{g.width}x{g.height} greyscale copy", "advisory": True}


def _flatten(img: PILImage.Image) -> PILImage.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = PILImage.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _jpeg(img: PILImage.Image, side: int, kind: str, q: int) -> Rendition:
    r = img.copy()
    r.thumbnail((side, side), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    r.save(buf, "JPEG", quality=q, optimize=True)       # no exif= / icc_profile= → the rendition carries no metadata
    data = buf.getvalue()
    return Rendition(kind, data, "image/jpeg", r.width, r.height, hashlib.sha256(data).hexdigest())


def validate(data: bytes) -> ValidatedMedia:
    """Validate an upload against the API-01 policy and build its renditions. Raises MediaRejected."""
    if not data:
        raise MediaRejected(422, E.MEDIA_INVALID, "The file is empty.", "empty")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            allowed = open_formats()
            probe = PILImage.open(io.BytesIO(data), formats=allowed)
            fmt = probe.format
            if fmt not in FORMATS:
                raise MediaRejected(415, E.UNSUPPORTED_MEDIA_TYPE, f"Use a {FORMAT_NAMES} image (this file is {fmt or 'not an image'}).")
            pw, ph = probe.size
            if pw * ph > settings.max_image_pixels:
                raise MediaRejected(422, E.MEDIA_INVALID, f"The image has {pw * ph / 1e6:.1f} megapixels; the limit is {settings.max_image_pixels / 1e6:.0f}.", "too_many_pixels")
            if fmt in ("JPEG", "MPO", "PNG", "WEBP", "BMP", "TIFF", "GIF"):
                probe.verify()                   # structural check; HEIF/AVIF decoders validate while decoding below
            img = PILImage.open(io.BytesIO(data), formats=allowed)
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)                      # animated GIF / multi-page TIFF / MPO: the first frame is the evidence
            img.load()
    except MediaRejected:
        raise
    except (PILImage.DecompressionBombError, PILImage.DecompressionBombWarning):
        raise MediaRejected(422, E.MEDIA_INVALID, "The image dimensions exceed the safety limit.", "too_many_pixels") from None
    except UnidentifiedImageError:
        raise MediaRejected(415, E.UNSUPPORTED_MEDIA_TYPE, f"The file content is not a recognised image. Use a {FORMAT_NAMES} image.") from None
    except Exception:
        raise MediaRejected(422, E.MEDIA_INVALID, "The image is corrupted or truncated.", "corrupted") from None
    capture = capture_metadata(img)
    try:
        oriented = ImageOps.exif_transpose(img)
    except Exception:
        oriented = img
    w, h = oriented.size
    if max(w, h) > settings.inspection_max_side:
        raise MediaRejected(422, E.MEDIA_INVALID, f"The image is {w}×{h} px; the limit is {settings.inspection_max_side} px per side.", "too_large_side")
    if min(w, h) < settings.inspection_min_side:
        raise MediaRejected(422, E.MEDIA_INVALID, f"The image is {w}×{h} px; the shorter side must be at least {settings.inspection_min_side} px.", "too_small")
    rgb = _flatten(oriented)
    if fmt in STORED_AS_IS:
        stored = data
        mime, ext = STORED_AS_IS[fmt]
    else:
        buf = io.BytesIO()
        (oriented if oriented.mode in ("RGB", "RGBA", "L", "LA") else oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")).save(buf, "PNG", optimize=True)
        stored = buf.getvalue()
        mime, ext = "image/png", "png"
        capture = {**capture, "source": {"format": fmt, "mime": FORMATS[fmt][0], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                         "stored_as": "lossless PNG of the first frame, EXIF orientation applied"}}
    v = ValidatedMedia(fmt=fmt, mime=mime, ext=ext, width=w, height=h, size_bytes=len(stored), sha256=hashlib.sha256(stored).hexdigest(),
                       capture=capture, quality=quality_metrics(rgb), stored=stored)
    v.renditions = [_jpeg(rgb, DISPLAY_SIDE, "display", 88), _jpeg(rgb, THUMB_SIDE, "thumb", 82)]
    return v
