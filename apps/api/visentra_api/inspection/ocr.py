"""Local OCR with bundled ONNX weights. No cloud upload or identity inference."""
from __future__ import annotations

import hashlib
import io
import math
import threading
from importlib.metadata import version

from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 12
MAX_PIXELS = 16_000_000
MAX_TEXT = 100_000
_engine = None
_slot = threading.Lock()


class ExtractionError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _open(data):
    """Only the inspection-image openers may read untrusted bytes (see media.open_formats); a well-formed image of
    another accepted type (GIF, HEIC, AVIF) still gets OCR_UNSUPPORTED below, anything else OCR_INVALID_FILE."""
    from .media import open_formats
    return Image.open(io.BytesIO(data), formats=open_formats())


def _image(data):
    try:
        im = _open(data)
        if im.format not in {"PNG", "JPEG", "WEBP", "TIFF", "BMP"}:
            raise ExtractionError("OCR_UNSUPPORTED", "Use JPEG, PNG, WebP, TIFF, BMP or PDF.")
        if im.width * im.height > MAX_PIXELS:
            raise ExtractionError("OCR_TOO_LARGE", "Text images are limited to 16 million pixels.")
        if getattr(im, "n_frames", 1) > 1:
            raise ExtractionError("OCR_UNSUPPORTED", "Upload multi-page scans as a PDF.")
        im.load()
        return ImageOps.exif_transpose(im).convert("RGB")
    except ExtractionError:
        raise
    except Exception as ex:
        raise ExtractionError("OCR_INVALID_FILE", "The image could not be safely decoded.") from ex


def validate(data):
    if not data or len(data) > MAX_BYTES:
        raise ExtractionError("OCR_TOO_LARGE", "Provide a nonempty file of at most 10 MB.")
    if data.startswith(b"%PDF-"):
        try:
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(data))
            if pdf.is_encrypted:
                raise ExtractionError("OCR_ENCRYPTED", "Encrypted PDFs are not supported.")
            if not 0 < len(pdf.pages) <= MAX_PAGES:
                raise ExtractionError("OCR_PAGE_LIMIT", "PDFs must contain 1–12 pages.")
        except ExtractionError:
            raise
        except ImportError as ex:
            raise ExtractionError("OCR_UNAVAILABLE", "PDF extraction dependencies are not installed.") from ex
        except Exception as ex:
            raise ExtractionError("OCR_INVALID_FILE", "The PDF could not be read.") from ex
        return "application/pdf"
    _image(data)
    with _open(data) as im:
        return Image.MIME.get(im.format, "application/octet-stream")


def _recognize(im, page):
    global _engine
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    if _engine is None:
        # Models ship inside the pinned wheel. No runtime download is needed.
        _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    im.thumbnail((2400, 2400))
    rows, _ = _engine(np.asarray(im)[:, :, ::-1].copy())
    lines = []
    for polygon, text, confidence in rows or []:
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ExtractionError("OCR_INVALID_OUTPUT", "OCR returned an invalid score.")
        if len(polygon) != 4 or any(not math.isfinite(float(v)) for point in polygon for v in point):
            raise ExtractionError("OCR_INVALID_OUTPUT", "OCR returned invalid text coordinates.")
        lines.append({"text": str(text), "confidence": confidence,
                      "polygon": [[float(x) / im.width, float(y) / im.height] for x, y in polygon]})
    return {"page": page, "method": "ocr", "text": "\n".join(x["text"] for x in lines),
            "lines": lines, "confidence": sum(x["confidence"] for x in lines) / len(lines) if lines else None}


def extract(data, mime):
    if validate(data) != mime:
        raise ExtractionError("OCR_INVALID_FILE", "The declared format does not match the source file.")
    if not _slot.acquire(timeout=1):
        raise ExtractionError("OCR_BUSY", "Text extraction is busy. Retry shortly.")
    try:
        pages = []
        if mime == "application/pdf":
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(data))
            rendered = None
            try:
                for n, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append({"page": n + 1, "method": "pdf_text", "text": text, "lines": [], "confidence": None})
                    else:
                        import pypdfium2
                        if rendered is None:
                            rendered = pypdfium2.PdfDocument(data)
                        rp = rendered[n]
                        try:
                            w, h = rp.get_size()
                            if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
                                raise ExtractionError("OCR_TOO_LARGE", "PDF page dimensions exceed the safe render limit.")
                            scale = min(2, 2400 / max(w, h))
                            bitmap = rp.render(scale=scale)
                            try:
                                pages.append(_recognize(bitmap.to_pil().convert("RGB"), n + 1))
                            finally:
                                bitmap.close()
                        finally:
                            rp.close()
            finally:
                if rendered is not None:
                    rendered.close()
        else:
            pages.append(_recognize(_image(data), 1))
        text = "\n\n".join(p["text"] for p in pages)
        if len(text) > MAX_TEXT:
            raise ExtractionError("OCR_TEXT_LIMIT", "Extracted text exceeds 100,000 characters; split this document.")
        methods = {p["method"] for p in pages}
        engine = ("RapidOCR " + version("rapidocr-onnxruntime") if "ocr" in methods else "PDF selectable text")
        if mime == "application/pdf":
            engine += " / pypdf " + version("pypdf")
        return {"pages": pages, "original_text": text, "status": "completed" if text.strip() else "no_text", "engine": engine}
    except ExtractionError:
        raise
    except ImportError as ex:
        raise ExtractionError("OCR_UNAVAILABLE", "Install the OCR dependencies before extracting text.") from ex
    except Exception as ex:
        raise ExtractionError("OCR_FAILED", "Text extraction failed; the source is preserved and can be retried.") from ex
    finally:
        _slot.release()


def model_inventory():
    from pathlib import Path
    import rapidocr_onnxruntime
    return [{"filename": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in Path(rapidocr_onnxruntime.__file__).parent.rglob("*.onnx")]
