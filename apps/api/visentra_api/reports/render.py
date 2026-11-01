"""Render a report snapshot (reports/service.py::snapshot) into a PDF. Pure: bytes in, bytes out; no database, no storage.

Layout rules (Phase 11 brief §11): images keep their aspect ratio and never exceed the page; tables repeat their header
after a page break; long text wraps; every page has a footer with the report identity and "page x of y"; missing optional
values are printed as "Not recorded" - nothing is invented.
"""
from __future__ import annotations

import io

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .pdfwriter import Doc, text_width, wrap

NAVY, MUTED, CYAN, RULE, BAND = "#0B1222", "#5B6B82", "#0B6FA4", "#C9D2DE", "#EEF2F7"
CAT_HEX = {"crack": "#E5484D", "dent": "#F5A524", "corrosion": "#8E4EC6", "other": "#6B7A90"}
CAT_LABEL = {"crack": "Crack", "dent": "Dent", "corrosion": "Corrosion", "other": "Other / unclassified"}
DEC_LABEL = {"confirmed": "Confirmed defect", "rejected": "Rejected", "further_review": "Needs further investigation", "pending": "Pending (unreviewed)"}
KIND_LABEL = {"decision": "Decision", "reclassify": "Reclassified", "note": "Note", "delete": "Finding deleted", "signoff": "Sign-off", "reopen": "Reopened"}
DISCLAIMER = ("VisionTra provides AI-assisted visual inspection support. Its predictions may be incomplete or incorrect and require review "
              "by qualified personnel. This report is not an airworthiness certificate, maintenance release, or independent confirmation "
              "that an aircraft is free of defects. It records what was examined and decided in VisionTra for the inspection revision named "
              "on every page; it does not establish the absence of defects anywhere on the aircraft.")
M = 40


def nr(v) -> str:
    return "Not recorded" if v in (None, "", []) else str(v)


def annotate(jpeg: bytes, items: list[dict]) -> tuple[bytes, int, int]:
    """Draw numbered boxes on a COPY of the (EXIF-free) display rendition. The stored original is never touched."""
    im = PILImage.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    lw = max(2, round(W / 300))
    fs = max(14, round(W / 45))
    try:
        font = ImageFont.load_default(size=fs)
    except TypeError:                                       # Pillow < 10.1: fixed-size bitmap font
        font = ImageFont.load_default()
    for it in items:
        b, col = it["box"], CAT_HEX.get(it["category"], "#6B7A90")
        x0, y0, x1, y1 = b["x"] * W, b["y"] * H, (b["x"] + b["w"]) * W, (b["y"] + b["h"]) * H
        width = max(1, lw // 2) if it.get("dim") else lw
        d.rectangle([x0, y0, x1, y1], outline=col, width=width)
        label = f"{it['n']}"
        l, t, r_, b_ = d.textbbox((0, 0), label, font=font)
        tw, th = r_ - l + fs * 0.6, b_ - t + fs * 0.5
        ty = y0 - th if y0 >= th else y1
        d.rectangle([x0, ty, x0 + tw, ty + th], fill=col)
        d.text((x0 + fs * 0.3 - l, ty + fs * 0.25 - t), label, fill="#0B1222", font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return buf.getvalue(), W, H


class Writer:
    def __init__(self, snap: dict):
        self.s = snap
        r, i = snap["report"], snap["inspection"]
        self.doc = Doc(title=f"VisionTra inspection report {i['ref']} R{r['version']}", author="VisionTra",
                       subject="AI-assisted aircraft surface inspection support report (not an airworthiness document)")
        self.Wd = self.doc.W - 2 * M
        self.y = 0.0
        self.new_page()

    # ---------------------------------------------------------------- page mechanics
    def new_page(self):
        self.doc.page()
        self.y = M

    def need(self, h: float):
        if self.y + h > self.doc.H - 56:
            self.new_page()
            return True
        return False

    def h2(self, t: str):
        self.need(40)
        self.y += 6
        self.doc.text(t, M, self.y + 12, 12.5, True, NAVY)
        self.y += 18
        self.doc.line(M, self.y, M + self.Wd, self.y, RULE, 0.6)
        self.y += 8

    def para(self, t: str, size=9.0, color=NAVY, bold=False):
        for ln in wrap(t, self.Wd, size, bold):
            self.need(size * 1.4)
            self.doc.text(ln, M, self.y + size, size, bold, color)
            self.y += size * 1.38
        self.y += 3

    def kv(self, rows: list[tuple[str, str]], key_w=130.0):
        for k, v in rows:
            lines = wrap(v, self.Wd - key_w, 9)
            self.need(len(lines) * 12.4 + 2)
            self.doc.text(k, M, self.y + 9, 8.5, True, MUTED)
            for j, ln in enumerate(lines):
                self.doc.text(ln, M + key_w, self.y + 9 + j * 12.4, 9, False, NAVY)
            self.y += max(1, len(lines)) * 12.4 + 2

    def table(self, cols: list[tuple[str, float]], rows: list[list[str]], size=8.0, colors: list[str | None] | None = None):
        total = sum(w for _, w in cols)
        cols = [(h, w * self.Wd / total) for h, w in cols]

        def head():
            self.doc.rect(M, self.y, self.Wd, 16, fill=BAND)
            x = M + 4
            for h, w in cols:
                self.doc.text(h, x, self.y + 11, 7.5, True, MUTED, max_width=w - 6)
                x += w
            self.y += 16
        wrapped = [[wrap(v, w - 8, size) for v, (_, w) in zip(row, cols)] for row in rows]
        heights = [max(len(c) for c in cells) * size * 1.3 + 7 for cells in wrapped]
        self.need(16 + (heights[0] if heights else 18))          # never leave a header alone at the foot of a page
        head()
        for ri, row in enumerate(rows):
            cells, rh = wrapped[ri], heights[ri]
            if self.y + rh > self.doc.H - 56:
                self.new_page()
                head()
            x = M + 4
            for k, (c, (_, w)) in enumerate(zip(cells, cols)):
                if k == 1 and colors and colors[ri]:
                    self.doc.rect(x, self.y + 5, 6, 6, fill=colors[ri])
                for j, ln in enumerate(c):
                    self.doc.text(ln, x + (9 if k == 1 and colors and colors[ri] else 0), self.y + 4 + size + j * size * 1.3, size, False, NAVY)
                x += w
            self.y += rh
            self.doc.line(M, self.y, M + self.Wd, self.y, "#E1E7EF", 0.5)
        self.y += 6

    def image_pair(self, a: tuple[bytes, int, int], b: tuple[bytes, int, int], cap_a: str, cap_b: str):
        gap = 12
        col_w = (self.Wd - gap) / 2
        ratio = a[2] / a[1]
        max_h = 300.0
        w = col_w
        h = w * ratio
        if h > max_h:                     # tall image: shrink both dimensions, never distort
            h = max_h
            w = h / ratio
        self.need(h + 30)
        self.doc.text(cap_a, M, self.y + 9, 8.5, True, MUTED)
        self.doc.text(cap_b, M + col_w + gap, self.y + 9, 8.5, True, MUTED)
        self.y += 14
        ia, ib = self.doc.add_jpeg(*a), self.doc.add_jpeg(*b)
        self.doc.image(ia, M, self.y, w, h)
        self.doc.rect(M, self.y, w, h, stroke=RULE, lw=0.6)
        self.doc.image(ib, M + col_w + gap, self.y, w, h)
        self.doc.rect(M + col_w + gap, self.y, w, h, stroke=RULE, lw=0.6)
        self.y += h + 8

    # ---------------------------------------------------------------- content
    def build(self) -> tuple[bytes, int]:
        s, d = self.s, self.doc
        r, i = s["report"], s["inspection"]
        d.text("VISIONTRA", M, self.y + 12, 11, True, CYAN)
        d.text("Aircraft surface inspection report", M, self.y + 32, 18, True, NAVY)
        d.text("AI-assisted inspection support - not an airworthiness document", M, self.y + 47, 9, False, MUTED)
        d.text(i["ref"], d.W - M, self.y + 12, 11, True, NAVY, align="right")
        d.text(f"Report R{r['version']} · inspection revision {r['inspection_revision']}", d.W - M, self.y + 27, 8.5, False, MUTED, align="right")
        d.text(f"Generated {r['generated_at']}", d.W - M, self.y + 40, 8.5, False, MUTED, align="right")
        self.y += 56
        d.line(M, self.y, d.W - M, self.y, RULE, 0.8)
        self.y += 10
        self.kv([("Report ID", r["id"]), ("Report version", f"R{r['version']} (template {r['template']}); pinned to inspection revision {r['inspection_revision']}"),
                 ("Report status", "Generated from the signed-off state of this inspection revision. A later change to the inspection makes this report stale; it is never edited."),
                 ("Inspection ID", i["id"]), ("Inspection created", f"{i['created_at']} by {nr(i['created_by'])}"), ("Workspace", nr(i["workspace"])),
                 ("Component", i["component_label"]), ("Zone / station", nr(i["zone"])), ("Aircraft reference", nr(i["aircraft_ref"])),
                 ("Inspection notes", nr(i["notes"]))])

        # ------------------------------------------------ source evidence
        self.h2("1. Source evidence")
        for m in s["media"]:
            cap = m["capture"] or {}
            self.kv([("Image file", f"{m['filename']} ({m['mime']}, {m['width']} x {m['height']} px, {m['size_bytes']:,} bytes)"),
                     ("Original SHA-256", f"{m['sha256']} (re-verified against the stored original when this report was generated)"),
                     ("Uploaded", f"{m['uploaded_at']} by {nr(m['uploaded_by'])}"),
                     ("Camera", nr(" ".join(x for x in (cap.get("camera_make"), cap.get("camera_model")) if x))),
                     ("Taken at (camera clock)", nr(cap.get("taken_at"))),
                     ("Location data", "GPS present in the original file; coordinates are not stored or reproduced by VisionTra" if cap.get("has_gps") else "None in the file"),
                     ("Evidence reference", f"media {m['id']} - the unmodified original is retrievable in VisionTra by authorised users")])
            self.image_pair((m["display_jpeg"], m["display_w"], m["display_h"]), (m["annotated_jpeg"], m["display_w"], m["display_h"]),
                            "Original image (metadata-free display copy)", "Annotated image (numbers refer to the tables below)")
        self.para("Boxes mark where a person or a model pointed; they are approximate and do not define the physical extent of a defect.", 8, MUTED)

        # ------------------------------------------------ AI analysis
        self.h2("2. AI analysis")
        a = s["analysis"]
        dt = s.get("detection") or {}
        if dt.get("state") == "relevance_rejected":
            self.para(f"Not analysed: the aircraft-surface check ({dt.get('gate_id') or 'domain gate'}) rejected this image "
                      f"({dt.get('gate_reason') or 'not an aircraft surface'}; p = {dt.get('gate_p')}). The defect detector did not run. "
                      "This is a manual-only inspection: every finding in section 3 was recorded by a person, and the absence of AI predictions is "
                      "not evidence that the surface is free of defects.")
        elif a is None:
            self.para("No operational defect-detection model analysed this inspection. Every finding in section 3 was recorded by a person. "
                      "The absence of AI predictions is not evidence that the surface is free of defects.")
        else:
            if dt.get("overridden"):
                self.para(f"The aircraft-surface check rejected this image and an engineer ran detection anyway"
                          f"{(' (reason: ' + dt['override_reason'] + ')') if dt.get('override_reason') else ''}. Treat these AI results with that caveat.", 8.5, MUTED)
            self.kv([("Model", f"{a['model_label']} (version id {a['model_version_id']})"), ("Analysis run", f"{a['run_id']} · attempt {a['attempt']} · finished {a['finished_at']}"),
                     ("Thresholds", f"score {a['score_threshold']}, IoU {a['iou_threshold']}, review {a['review_threshold']}"), ("Taxonomy", a["taxonomy_version"])])
            if s["predictions"]:
                self.table([("#", 16), ("Class", 50), ("Prediction ID", 152), ("Score · AI tier", 92), ("Location (px)", 90), ("Review", 95)],
                           [[str(p["n"]), CAT_LABEL.get(p["class_id"], p["class_id"]), p["id"], f"{p['score']:.2f}" + (f" · {p['tier']}" if p.get("tier") else (" (low)" if p["low_confidence"] else "")),
                             p["location"], DEC_LABEL[p["decision"]] + (f" -> {CAT_LABEL[p['corrected_class_id']]}" if p.get("corrected_class_id") else "")] for p in s["predictions"]],
                           colors=[CAT_HEX.get(p["class_id"]) for p in s["predictions"]], size=7.5)
                self.para("Confidence is an uncalibrated model score for its own box, not a measured probability. It is not a defect grade, severity or airworthiness risk."
                          " The AI tier (likely / possible / unlikely) states how often boxes of that class and score band were a real defect on the model's validation images.", 8, MUTED)
            else:
                self.para("The model returned no prediction above its threshold. This does not establish that the surface is free of defects.")
        if s["experimental_runs"]:
            self.para(f"{s['experimental_runs']} experimental model run(s) (development-only, dent-only research model) exist for this inspection's image. "
                      "By policy (ADR-014) experimental output is never an inspection finding and is excluded from reports.", 8.5, MUTED)

        # ------------------------------------------------ human review
        self.h2("3. Human review")
        so = s["signoff"]
        self.kv([("Signed off", f"{so['at']} by {nr(so['by'])}"), ("Outcome", so["outcome_label"]), ("Sign-off note", nr(so["note"])),
                 *([("Acknowledgement", so["acknowledgement"])] if so.get("acknowledgement") else [])])
        rows, colors = [], []
        for f in s["findings"]:
            ai = f.get("ai_class")
            cat = CAT_LABEL.get(f["category"], f["category"]) + (f" (AI: {CAT_LABEL.get(ai, ai)})" if ai and ai != f["category"] else "")
            rows.append([str(f["n"]), cat if f["decision"] != "pending" else f"{cat} (AI proposal, not reviewed)", f["source"], f["location"], DEC_LABEL[f["decision"]],
                         f"{nr(f['decided_by'])}, {nr(f['decided_at'])}", nr(f["comment"])])
            colors.append(CAT_HEX.get(f["category"]))
        if rows:
            self.table([("#", 16), ("Category", 64), ("Source", 52), ("Location (px)", 84), ("Decision", 72), ("Decided by / at", 84), ("Comment", 120)], rows, colors=colors)
        else:
            self.para("No finding was recorded for this inspection revision.")
        outstanding = [f for f in s["findings"] if f["decision"] == "further_review"]
        if outstanding:
            self.para("Outstanding: " + "; ".join(f"#{f['n']} {CAT_LABEL.get(f['category'], f['category'])} needs further investigation" for f in outstanding) + ".", 9, NAVY, True)
        if so["outcome"] == "no_confirmed_findings":
            self.para("No defect was confirmed among the findings considered. This does not establish that the inspected surface, or the aircraft, is free of defects.", 9)
        if s["deleted_findings"]:
            self.para(f"{s['deleted_findings']} reviewer finding(s) were deleted during review; they remain in the review history below.", 8, MUTED)

        # ------------------------------------------------ guidance (Phase 16; scientific review 2026-10-29)
        # Class-specific evidence steps only for a class an engineer confirmed (a confirmed or reclassified finding); a
        # further-review category's class is not established. No class gets repair options: confirmation is a classification,
        # not a disposition, and VisionTra holds no aircraft-specific approved data.
        cats = []
        for f in s["findings"]:
            if f["decision"] == "confirmed" and f["category"] not in cats:
                cats.append(f["category"])
        unconfirmed = []
        for f in s["findings"]:
            if f["decision"] == "further_review" and f["category"] not in cats and f["category"] not in unconfirmed:
                unconfirmed.append(f["category"])
        if cats or unconfirmed:
            from ..inspection import guidance as G
            self.h2("3a. Maintenance guidance for the recorded findings (reference only)")
            self.para(G.DISCLAIMER, 8, MUTED)
            self.para("Engineering disposition: not established. " + G.DISPOSITION_GATE + " Aircraft-specific acceptance limits and the applicable "
                      "SRM/AMM or approved data are not held by VisionTra and are not stated in this report.", 8.5, NAVY, True)
            if unconfirmed:
                self.para("Not confirmed, needs further investigation: " + ", ".join(CAT_LABEL.get(c, c) for c in unconfirmed)
                          + ". The defect type is not established, so no type-specific next steps are given for it.", 8.5, NAVY, True)
            for c in cats:
                g = G.for_class(c)
                self.para(g["label"] + " (class confirmed by the reviewer)", 9.5, NAVY, True)
                self.para(g["what_it_is"], 8.5)
                self.para("Not established from the image:", 8.5, NAVY, True)
                for t in g["unknowns"]:
                    self.para("- " + t, 8.5)
                self.para("Evidence still to collect:", 8.5, NAVY, True)
                for t in g["immediate_actions"]:
                    self.para("- " + t, 8.5)
                self.para("Prevention (general background, not specific to this finding):", 8.5, NAVY, True)
                for t in g["prevention"]:
                    self.para("- " + t, 8.5)
                self.para("Sources: " + "; ".join(f"{x['title']} ({x['revision'] or 'revision not stated'}; {x['supports']})" for x in g["sources"]), 7.5, MUTED)

        # OCR evidence is documentary context, never a defect finding.
        if s.get("text_evidence"):
            self.h2("3b. Extracted text evidence")
            self.para("Documentary evidence only: not an AI defect finding or a human-confirmed defect. Original extraction and human corrections are shown separately.", 8, MUTED)
            self.para("PDF text uses WinAnsi: unsupported characters display as ?. Consult the original source and persisted Unicode text for exact transcription.", 8, MUTED)
            for ev in s["text_evidence"]:
                self.para(ev["filename"], 10, NAVY, True)
                self.kv([("Evidence ID / revision", f"{ev['id']} / {ev['revision']}"),
                         ("Document revision", nr(ev.get("document_revision"))),
                         ("Source SHA-256", ev["sha256"]),
                         ("Source MIME", ev["mime"]), ("Engine", ev["engine"]),
                         ("Extracted at", nr(ev.get("created_at"))),
                         ("Status", ev["status"]), ("Error", nr(ev.get("error_code")))])
                self.para("Original extracted text", 9, NAVY, True)
                self.para(ev["original_text"] or "No extracted text recorded.")
                for page in ev.get("pages", []):
                    self.para(f"Page {page.get('page', '?')} - method: {page.get('method', 'not recorded')}", 8, MUTED)
                    self.para(page.get("text", "") or "No page text recorded.", 8)
                if ev.get("corrected_text") is not None:
                    self.para("Human-corrected text (whole document; not a page-level correction)", 9, NAVY, True)
                    self.para(ev["corrected_text"] or "Empty correction confirmed.")
                    self.para(f"Confirmed by account {ev.get('confirmed_by')} at {ev.get('confirmed_at')}", 8, MUTED)

        # ------------------------------------------------ history
        self.h2("4. Review history (append-only)")
        if s["events"]:
            self.table([("Rev.", 30), ("When (UTC)", 92), ("Who", 80), ("Action", 70), ("Target", 70), ("Result", 82), ("Comment", 130)],
                       [[str(e["revision"]), e["at"], nr(e["actor"]), KIND_LABEL.get(e["kind"], e["kind"]), e["target"], e["result"], nr(e["comment"])] for e in s["events"]], size=7.5)
        else:
            self.para("No review event.")

        # ------------------------------------------------ limitations
        self.h2("5. Limitations")
        for t in s["limitations"]:
            self.para("- " + t, 8.5)
        self.need(70)
        box_lines = wrap(DISCLAIMER, self.Wd - 20, 8.5)
        bh = len(box_lines) * 11.5 + 14
        d.rect(M, self.y, self.Wd, bh, fill="#FFF7E6", stroke="#F0C36D", lw=0.6)
        for j, ln in enumerate(box_lines):
            d.text(ln, M + 10, self.y + 14 + j * 11.5, 8.5, False, NAVY)
        self.y += bh + 6

        n = len(d.pages)
        for k in range(n):        # footer with the total page count (second pass)
            d.line(M, d.H - 38, d.W - M, d.H - 38, RULE, 0.6, page=k)
            d.text(f"VisionTra · {i['ref']} · report {r['id'][:8]} R{r['version']} · inspection revision {r['inspection_revision']} · not an airworthiness document",
                   M, d.H - 24, 7.5, False, MUTED, max_width=d.W - 2 * M - 70, page=k)
            d.text(f"Page {k + 1} of {n}", d.W - M, d.H - 24, 7.5, False, MUTED, align="right", page=k)
        return d.output(), n


def render(snapshot: dict) -> tuple[bytes, int]:
    for m in snapshot["media"]:
        items = [{"n": f["n"], "box": f["box"], "category": f["category"], "dim": f["decision"] == "rejected"} for f in snapshot["findings_for_media"].get(m["id"], [])]
        m["annotated_jpeg"], _, _ = annotate(m["display_jpeg"], items)
    return Writer(snapshot).build()


__all__ = ["render", "annotate", "text_width", "DISCLAIMER"]
