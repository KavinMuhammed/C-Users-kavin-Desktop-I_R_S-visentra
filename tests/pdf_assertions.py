"""Extract actual PDF content using the application's installed pypdf dependency."""
import io
import re
from pypdf import PdfReader


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data), strict=True)
    assert len(reader.pages) > 0
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))
