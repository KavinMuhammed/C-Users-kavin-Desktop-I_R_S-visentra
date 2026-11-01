"""Minimal, dependency-free PDF 1.4 writer — a Python port of the frontend's apps/web/src/lib/pdf.js (same features, same
limits), so server and browser reports are produced the same way without a new third-party PDF dependency.

Supports A4 pages, Helvetica / Helvetica-Bold text in WinAnsi encoding, wrapped paragraphs, rectangles, lines and embedded
baseline JPEG images. Coordinates are points with the origin at the TOP-LEFT of the page.
Text is always written as an escaped PDF literal string: untrusted input (comments, file names) cannot inject PDF operators.
Characters outside WinAnsi are replaced by '?' (documented limitation).
"""
from __future__ import annotations

from datetime import datetime, timezone

HW = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
WIN = {"–": 0x96, "—": 0x97, "‘": 0x91, "’": 0x92, "“": 0x93, "”": 0x94, "•": 0x95, "…": 0x85, "€": 0x80, "™": 0x99}
SUB = {"→": "->", "←": "<-", "≤": "<=", "≥": ">=", "≈": "~", "−": "-", " ": " ", " ": " ", " ": " ", "×": "x"}


def to_win(s) -> list[int]:
    out: list[int] = []
    for ch in str("" if s is None else s):
        if ch in SUB:
            out.extend(ord(c) for c in SUB[ch])
            continue
        c = ord(ch)
        if ch in WIN:
            out.append(WIN[ch])
        elif 32 <= c <= 126 or 0xA0 <= c <= 0xFF:
            out.append(c)
        elif c in (9, 10):
            out.append(32)
        else:
            out.append(63)
    return out


def _cw(c: int) -> int:
    return HW[c - 32] if 32 <= c <= 126 else 556


def text_width(s, size: float, bold: bool = False) -> float:
    return sum(_cw(c) for c in to_win(s)) / 1000 * size * (1.06 if bold else 1)


def lit(s) -> str:
    o = ["("]
    for c in to_win(s):
        if c in (40, 41, 92):
            o.append("\\" + chr(c))
        elif c < 32 or c > 126:
            o.append("\\" + format(c, "03o"))
        else:
            o.append(chr(c))
    return "".join(o) + ")"


def num(n: float) -> str:
    v = round(n * 100) / 100
    return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0")


def rgb(c) -> tuple[float, float, float]:
    if isinstance(c, (tuple, list)):
        return tuple(c)  # type: ignore[return-value]
    h = str(c or "#000").lstrip("#")
    if len(h) == 3:
        h = "".join(x * 2 for x in h)
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def wrap(text, width: float, size: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for para in str("" if text is None else text).split("\n"):
        words = [w for w in para.split() if w]
        if not words:
            lines.append("")
            continue
        line = ""
        for w in words:
            t = f"{line} {w}" if line else w
            if text_width(t, size, bold) <= width or not line:
                if not line and text_width(w, size, bold) > width:      # hard-break very long tokens (hashes, ids)
                    chunk = ""
                    for ch in w:
                        if text_width(chunk + ch, size, bold) > width:
                            lines.append(chunk)
                            chunk = ""
                        chunk += ch
                    line = chunk
                else:
                    line = t
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


def truncate(s, w: float, size: float, bold: bool = False) -> str:
    s = str(s)
    if text_width(s, size, bold) <= w:
        return s
    while len(s) > 1 and text_width(s + "...", size, bold) > w:
        s = s[:-1]
    return s + "..."


class Doc:
    def __init__(self, title: str = "", author: str = "", subject: str = "", width: float = 595.28, height: float = 841.89):
        self.W, self.H = width, height
        self.title, self.author, self.subject = title, author, subject
        self.pages: list[list[str]] = []
        self.images: list[dict] = []
        self.cur: list[str] | None = None

    def page(self) -> "Doc":
        self.cur = []
        self.pages.append(self.cur)
        return self

    def op(self, s: str, page: int | None = None) -> None:
        if page is not None:
            self.pages[page].append(s)
            return
        if self.cur is None:
            self.page()
        self.cur.append(s)

    def text(self, s, x, y, size=10.0, bold=False, color="#111111", align="left", max_width=None, page=None) -> "Doc":
        if max_width:
            s = truncate(s, max_width, size, bold)
        tx = x
        if align != "left":
            w = text_width(s, size, bold)
            tx = x - w if align == "right" else x - w / 2
        r, g, b = rgb(color)
        self.op(f"BT /{'F2' if bold else 'F1'} {num(size)} Tf {num(r)} {num(g)} {num(b)} rg {num(tx)} {num(self.H - y)} Td {lit(s)} Tj ET", page)
        return self

    def para(self, s, x, y, width, size=10.0, bold=False, color="#111111", leading=1.35) -> float:
        lines = wrap(s, width, size, bold)
        for i, ln in enumerate(lines):
            self.text(ln, x, y + i * size * leading, size, bold, color)
        return y + len(lines) * size * leading

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.8, dash=None, page=None) -> "Doc":
        s = "q "
        if fill:
            r, g, b = rgb(fill)
            s += f"{num(r)} {num(g)} {num(b)} rg "
        if stroke:
            r, g, b = rgb(stroke)
            s += f"{num(r)} {num(g)} {num(b)} RG {num(lw)} w "
        if dash:
            s += f"[{' '.join(num(d) for d in dash)}] 0 d "
        s += f"{num(x)} {num(self.H - y - h)} {num(w)} {num(h)} re {'B' if fill and stroke else 'f' if fill else 'S'} Q"
        self.op(s, page)
        return self

    def line(self, x1, y1, x2, y2, color="#999999", lw=0.6, page=None) -> "Doc":
        r, g, b = rgb(color)
        self.op(f"q {num(r)} {num(g)} {num(b)} RG {num(lw)} w {num(x1)} {num(self.H - y1)} m {num(x2)} {num(self.H - y2)} l S Q", page)
        return self

    def add_jpeg(self, data: bytes, px_w: int, px_h: int) -> str:
        if not (data and len(data) > 3 and data[0] == 0xFF and data[1] == 0xD8):
            raise ValueError("image is not a JPEG stream")
        name = f"Im{len(self.images) + 1}"
        self.images.append({"name": name, "bytes": data, "w": px_w, "h": px_h})
        return name

    def image(self, name, x, y, w, h) -> "Doc":
        self.op(f"q {num(w)} 0 0 {num(h)} {num(x)} {num(self.H - y - h)} cm /{name} Do Q")
        return self

    def output(self) -> bytes:
        if not self.pages:
            self.page()
        objs: list = []

        def add(o) -> int:
            objs.append(o)
            return len(objs)
        catalog, pages_obj, f1, f2, info = add(None), add(None), add(None), add(None), add(None)
        img_ids = [add(im) for im in self.images]
        page_ids = []
        for ops in self.pages:
            content = add({"stream": "\n".join(ops)})
            page_ids.append(add({"page": True, "content": content}))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%SZ")

        def body(i: int) -> str:
            o = objs[i - 1]
            if i == catalog:
                return f"<< /Type /Catalog /Pages {pages_obj} 0 R >>"
            if i == pages_obj:
                return f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>"
            if i == f1:
                return "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
            if i == f2:
                return "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
            if i == info:
                return (f"<< /Title {lit(self.title)} /Author {lit(self.author)} /Subject {lit(self.subject)} "
                        f"/Producer (VisionTra server report writer) /CreationDate (D:{stamp}) >>")
            xo = " ".join(f"/{im['name']} {img_ids[k]} 0 R" for k, im in enumerate(self.images))
            return (f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {num(self.W)} {num(self.H)}] "
                    f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << {xo} >> >> /Contents {o['content']} 0 R >>")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * (len(objs) + 1)
        for i in range(1, len(objs) + 1):
            offsets[i] = len(out)
            o = objs[i - 1]
            if isinstance(o, dict) and "stream" in o:
                s = o["stream"].encode("latin-1")
                out += f"{i} 0 obj\n<< /Length {len(s)} >>\nstream\n".encode() + s + b"\nendstream\nendobj\n"
            elif isinstance(o, dict) and "bytes" in o:
                out += (f"{i} 0 obj\n<< /Type /XObject /Subtype /Image /Width {o['w']} /Height {o['h']} /ColorSpace /DeviceRGB "
                        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(o['bytes'])} >>\nstream\n").encode() + o["bytes"] + b"\nendstream\nendobj\n"
            else:
                out += f"{i} 0 obj\n{body(i)}\nendobj\n".encode("latin-1")
        xref = len(out)
        x = [f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"] + [f"{offsets[i]:010d} 00000 n \n" for i in range(1, len(objs) + 1)]
        out += "".join(x).encode() + f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
