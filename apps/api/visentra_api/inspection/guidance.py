"""Class-level reference content for AI-proposed and reviewer-recorded findings (dent, crack, corrosion, other).

Scientific review (2026-10-29, docs/PROPOSAL_ENGINE_SCIENTIFIC_REVIEW.md) replaced the Phase 16/19 text. The content now keeps
apart what earlier versions mixed together:

  · morphology   — what a reviewer may be able to see (``types``); not ranked, no severity attached
  · mechanism    — possible causes (``mechanisms``); never established by a photograph
  · context      — circumstances that may call for more detailed assessment (``severity_factors``, kept as the API key);
                   none of them sets a severity or a disposition on its own
  · evidence     — how to obtain better evidence (``immediate_actions``, ``inspection_methods``); no NDT method is prescribed
  · disposition  — always deferred to the applicable aircraft-specific data (``repair_options``, kept as the API key, now holds
                   no repair method at all)

VisionTra holds no aircraft-specific maintenance data (SRM, AMM, ICA, approved repair data), so it never states an acceptance
limit, repair method, inspection interval or numerical threshold. References are background unless stated otherwise, and
each says what it supports and where it does not apply. Source metadata was checked on the issuing authority's own page on
the ``verified_on`` date; anything not checked there is marked unverified.
"""
from __future__ import annotations

VERIFIED_ON = "2026-10-29"
SOURCES = [
    {"id": "AC43.13-1B", "title": "FAA AC 43.13-1B — Acceptable Methods, Techniques, and Practices: Aircraft Inspection and Repair",
     "org": "U.S. Federal Aviation Administration (AFS-300)", "number": "AC 43.13-1B", "revision": "Includes Change 1; status Active",
     "date": "1998-09-08", "role": "background",
     "url": "https://www.faa.gov/regulations_policies/advisory_circulars/index.cfm/go/document.information/documentID/99861",
     "scope": "Methods acceptable to the FAA for inspection and repair of nonpressurized areas of civil aircraft, only when there are no "
              "manufacturer repair or maintenance instructions, and only where the user has determined it is appropriate, directly applicable "
              "and not contrary to manufacturer's data. Not a universal dent or damage acceptance standard.",
     "supports": "Background on general inspection practice. It does not supply acceptance limits or repairs for a specific aircraft, "
                 "pressurized structure, or any part covered by manufacturer instructions.",
     "verified_on": VERIFIED_ON, "section": None},
    {"id": "14CFR43.13", "title": "14 CFR § 43.13 — Performance rules (general)", "org": "U.S. Code of Federal Regulations (FAA)",
     "number": "14 CFR 43.13", "revision": "As amended through Amdt. 43-63 (90 FR 35211, 2025-07-24)", "date": "2025-07-24", "role": "regulatory_context",
     "url": "https://www.law.cornell.edu/cfr/text/14/43.13",
     "scope": "U.S. requirement: maintenance uses the current manufacturer's maintenance manual or Instructions for Continued Airworthiness, or "
              "other methods acceptable to the Administrator, and restores at least the original or properly altered condition.",
     "supports": "Why VisionTra defers acceptance limits and repairs to the manufacturer's data and the applicable maintenance process (U.S. operations).",
     "verified_on": VERIFIED_ON, "section": "(a), (b)"},
    {"id": "AC120-77B", "title": "FAA AC 120-77B — Performance Rules: Provisions Contained in an Operator Manual Under § 43.13(c)",
     "org": "U.S. Federal Aviation Administration (AFS-300)", "number": "AC 120-77B", "revision": "Active; cancels AC 120-77A (cancelled 2026-09-10)",
     "date": "2026-09-10", "role": "regulatory_context",
     "url": "https://www.faa.gov/regulations_policies/advisory_circulars/index.cfm/go/document.information/documentNumber/120-77B",
     "scope": "One means of ensuring maintenance, alteration or continue-in-service conditions comply with regulations, for operators using § 43.13(c).",
     "supports": "Context only: operator-manual provisions govern maintenance data for those operators. The AC text itself was not retrieved; no "
                 "statement about its content beyond the FAA summary is made.",
     "verified_on": VERIFIED_ON, "section": None},
    {"id": "AC43-4B", "title": "FAA AC 43-4B — Corrosion Control for Aircraft", "org": "U.S. Federal Aviation Administration (AFS-300)",
     "number": "AC 43-4B", "revision": "Active (with editorial updates)", "date": "2018-09-11", "role": "background",
     "url": "https://www.faa.gov/regulations_policies/advisory_circulars/index.cfm/go/document.information/documentNumber/43-4B",
     "scope": "Guidance for developing a corrosion control program; applies where the manufacturer has not published corrosion control information. "
              "A manufacturer's published program takes precedence.",
     "supports": "Background on corrosion identification. It does not set the corrosion level, blend-out limit or treatment for a specific aircraft.",
     "verified_on": VERIFIED_ON, "section": None},
    {"id": "AC20-107B", "title": "FAA AC 20-107B — Composite Aircraft Structure", "org": "U.S. Federal Aviation Administration (AIR-100)",
     "number": "AC 20-107B", "revision": "Active (Change 1 available)", "date": "2009-09-08", "role": "background",
     "url": "https://www.faa.gov/regulations_policies/advisory_circulars/index.cfm/go/document.information/documentNumber/20-107B",
     "scope": "Type-certification compliance guidance for composite structure under 14 CFR parts 23, 25, 27 and 29, with related design, "
              "manufacturing and maintenance aspects. Not a field inspection or repair procedure.",
     "supports": "Background only: composite parts can carry damage that is not visible at the surface.",
     "verified_on": VERIFIED_ON, "section": None},
    {"id": "EASA-CAW", "title": "EASA Easy Access Rules for Continuing Airworthiness (Regulation (EU) No 1321/2014)",
     "org": "European Union Aviation Safety Agency", "number": "Regulation (EU) No 1321/2014", "revision": "Revision from September 2025 (latest shown when checked)",
     "date": "2025-09-10", "role": "regulatory_context",
     "url": "https://www.easa.europa.eu/en/document-library/easy-access-rules/online-publications/easy-access-rules-continuing-airworthiness",
     "scope": "EU continuing-airworthiness rules (Part-M, Part-ML, Part-145, Part-CAMO, Part-CAO, Part-66 and others).",
     "supports": "Context only for EASA-regulated operations: the applicable process, not VisionTra, governs damage assessment and release.",
     "verified_on": VERIFIED_ON, "section": None},
    {"id": "DGCA", "title": "Directorate General of Civil Aviation (India) — regulations and guidance",
     "org": "Directorate General of Civil Aviation, Government of India", "number": None, "revision": None, "date": None, "role": "regulatory_context",
     "url": "https://www.dgca.gov.in/",
     "scope": "Official DGCA site; its Regulations and Guidance section lists the Civil Aviation Requirements. Individual CAR sections were not reviewed.",
     "supports": "Context only for DGCA-regulated operations. No CAR content is cited.",
     "verified_on": VERIFIED_ON, "section": None},
]
SOURCE_BY_ID = {s["id"]: s for s in SOURCES}
DISCLAIMER = ("Reference information only. VisionTra holds no aircraft-specific maintenance data: acceptance limits, repairs and return-to-service "
              "decisions come from the applicable SRM/AMM or other approved data and the operator's maintenance process, decided by qualified "
              "personnel. VisionTra cannot measure depth, length or material loss from a photo.")
DISPOSITION_GATE = ("No engineering disposition is recorded in VisionTra. Confirming or correcting a class does not establish an acceptable "
                    "damage limit and does not authorise a repair.")
_NDT = ("Any non-destructive inspection is selected by the applicable instructions for that location and material and performed by appropriately "
        "qualified personnel under an approved procedure. VisionTra does not select an NDT method.")
_MEASURE = ("Take dimensional measurements only with the method and tools that the applicable instructions specify; record them with the "
            "finding. VisionTra does not measure.")
_LOCATE = "Record the aircraft (registration or serial number), the component and the exact location in the aircraft's own reference system (station, stringer, zone)."
_REFER = "Refer the finding to a qualified inspector, particularly when the class is uncertain or any of the context indicators applies."

GUIDANCE: dict[str, dict] = {
    "dent": {
        "label": "Dent",
        "what_it_is": "Model class 'dent': a region the model associates with a local deformation of the skin contour. Its training labels "
                      "(VT-DS-001) also cover hail dents, impact damage and buckling, without any severity grading. The class name does not "
                      "establish depth, material loss, cracking, cause or structural significance.",
        "mechanisms": ["Dents in general can result from impact (for example hail, bird strike, ground equipment, tools or work stands) or from "
                       "overload deformation. A photograph does not establish which, if any, applies here.",
                       "Record a cause only when independent evidence supports it (an event report, a maintenance record, or the documented "
                       "distribution of damage across the aircraft). Several similar depressions on one image do not by themselves establish hail."],
        "immediate_actions": [
            "Re-photograph the area: one close-up and one wider view that shows nearby fasteners, panel edges or other features for context.",
            "Use low-angle (raking) light from more than one direction to clarify whether the contour is actually deformed; glare and reflections can both mimic and hide dents.",
            _LOCATE, _MEASURE,
            "Identify the applicable aircraft-specific data (SRM, AMM or other approved data) for that location and material; it determines further examinations and acceptance limits.",
            _REFER,
        ],
        "repair_options": [
            "VisionTra provides no repair option. Acceptance limits and any repair come from the applicable aircraft-specific data for the documented location and material.",
            DISPOSITION_GATE,
            "FAA AC 43.13-1B applies only to nonpressurized areas when no manufacturer repair or maintenance instructions exist, under its stated conditions; it is not a universal dent acceptance standard.",
        ],
        "prevention": [
            "General background, not specific to this finding: ground-handling procedures (wing walkers, equipment stop markers, protective bumpers) reduce contact damage.",
            "Tool control and padded work platforms during maintenance reduce accidental impact.",
        ],
        "refs": ["AC43.13-1B", "14CFR43.13"],
    },
    "crack": {
        "label": "Crack",
        "what_it_is": "Model class 'crack': a region the model associates with a separation of material. Its training labels (VT-DS-001) also mark "
                      "tears, holes and other skin ruptures, so read it as 'crack or skin rupture'. The model cannot distinguish a crack in the "
                      "material from a crack in paint or sealant, and cannot show a crack's full length.",
        "mechanisms": ["Cracks in general can result from fatigue, stress corrosion or a single overload event. A photograph does not establish which "
                       "applies; that needs the inspection history and a qualified assessment."],
        "immediate_actions": [
            "Re-photograph perpendicular to the surface with even light, after cleaning if permitted; dirt, fluid trails and scratches mimic cracks.",
            "Record the visible extent, orientation and visible end points as observations; a photograph cannot show the true length or tip location.",
            _LOCATE,
            "Identify the applicable aircraft-specific data and any Airworthiness Directive or Service Bulletin for that location.",
            _NDT, _REFER,
        ],
        "repair_options": [
            "VisionTra provides no repair option. Any temporary or permanent action comes from the applicable aircraft-specific data or an engineering disposition.",
            DISPOSITION_GATE,
        ],
        "prevention": [
            "General background, not specific to this finding: scheduled structural inspection programmes and correct fastener installation reduce crack initiation.",
        ],
        "refs": ["AC43.13-1B", "14CFR43.13"],
    },
    "corrosion": {
        "label": "Corrosion",
        "what_it_is": "Model class 'corrosion': a region the model associates with corrosion. It was learned from one Boeing 747 (VT-DS-002 'Rust', "
                      "drone frames); results on other aircraft, paint systems and corrosion types are unknown. The class does not establish the "
                      "corrosion type, depth or material loss.",
        "mechanisms": ["Corrosion forms are electro-chemical (for example surface, pitting, filiform, intergranular or exfoliation, galvanic, crevice, "
                       "fretting). Which form is present cannot be established from a photograph."],
        "immediate_actions": [
            "Re-photograph close up with even light; include fastener heads and lap edges and note any visible blistering, bulging or powder.",
            _LOCATE,
            "Identify the applicable corrosion data: the manufacturer's corrosion programme or the operator's corrosion prevention and control programme takes precedence.",
            "Material loss and remaining thickness need measurement under the applicable procedure after the approved removal method; VisionTra does not measure.",
            _REFER,
        ],
        "repair_options": [
            "VisionTra provides no treatment or repair option. Blend-out limits, treatment and corrosion level come from the applicable programme and data.",
            DISPOSITION_GATE,
        ],
        "prevention": [
            "General background, not specific to this finding: clear drainage, intact paint and sealant, and scheduled washing reduce corrosion.",
            "Dissimilar-metal contact without isolation promotes galvanic corrosion.",
        ],
        "refs": ["AC43-4B", "14CFR43.13"],
    },
}
GUIDANCE["other"] = {
    "label": "Other",
    "what_it_is": "Something the reviewer recorded that is not a dent, crack or corrosion (the three classes the AI was trained on) — for example "
                  "staining, a scratch, paint damage, a loose fastener, an old repair, or no damage at all. The type chosen by the reviewer is shown "
                  "next to the finding.",
    "mechanisms": [],
    "immediate_actions": ["Choose the specific type (or describe it in the note) and record the location.", _MEASURE,
                          "Disposition follows the applicable SRM/AMM or other approved data."],
    "repair_options": ["VisionTra provides no repair option.", DISPOSITION_GATE],
    "prevention": ["Per the operator's maintenance programme."],
    "refs": ["14CFR43.13"],
}

# Observable descriptors (``types``), contextual indicators (``severity_factors``) and further examinations (``inspection_methods``).
# Descriptors are prompts for what a reviewer may see; record one only when the image actually shows it. They are not ranked.
EXPERT: dict[str, dict] = {
    "dent": {
        "types": [
            "Apparent local depression of the surface contour (seen as a change in reflection or shading).",
            "Visible sharp contour, fold or crease within the region. A crease is an observation that needs further assessment; it does not establish that a crack exists.",
            "Visible scratch, gouge or other surface discontinuity in or next to the region.",
            "Visible paint or coating cracking in or around the region.",
            "Apparent opening, tear or puncture. If seen, the finding is not a dent alone; consider reclassifying it as crack or skin rupture.",
            "Apparent buckling or wrinkling across a wider area.",
            "More than one similar-looking region on the image.",
            "Visibility insufficient to tell these apart (lighting, angle, focus or distance).",
        ],
        "inspection_methods": [
            "Detailed visual inspection by a qualified person with suitable lighting and viewing angles.",
            "Dimensional measurement (depth, length, width and distance to nearby features) with the method the applicable instructions specify.",
            _NDT,
        ],
        "severity_factors": [
            "A visible sharp crease or fold.",
            "Visible cracking of paint, coating or material in or around the deformation.",
            "Combination with a scratch, gouge, puncture or other damage.",
            "A documented location on or near a frame, stringer, rib, fastener row, edge or joint (only when the location is recorded, never inferred from a close-up).",
            "A documented location in a pressurized area or on a composite part.",
            "Several findings close together.",
            "Evidence of previous damage or repair in the area.",
        ],
    },
    "crack": {
        "types": [
            "Visible linear discontinuity in the surface.",
            "Visible discontinuity starting at a fastener, hole, edge or corner (only if that feature is in the image).",
            "Several discontinuities in the same feature.",
            "Visible opening, tear or puncture (the training class includes skin ruptures).",
            "Discontinuity that may be confined to paint or sealant; the material beneath cannot be assessed from the image.",
            "Visibility insufficient to tell a crack from a scratch, panel line, hair or fluid trail.",
        ],
        "inspection_methods": [
            "Detailed visual inspection by a qualified person after cleaning, with suitable magnification and lighting.",
            "Record visible length, direction and visible end points as observations; the true extent needs the applicable inspection procedure.",
            _NDT,
        ],
        "severity_factors": [
            "A documented location in primary structure or a pressurized skin.",
            "A discontinuity starting at a fastener hole or edge.",
            "Several discontinuities in the same feature.",
            "Association with corrosion.",
            "Growth compared with a previous inspection record.",
        ],
    },
    "corrosion": {
        "types": [
            "Visible discolouration, etching or powder deposits.",
            "Visible pitting.",
            "Visible blistering or lifting of paint.",
            "Visible lifting of the surface in layers, often around fasteners.",
            "Visible bulging (pillowing) along a lap joint.",
            "Thread-like trails under paint.",
            "Visibility insufficient to tell corrosion from staining, sealant or primer.",
        ],
        "inspection_methods": [
            "Detailed visual inspection by a qualified person after cleaning, as the applicable programme specifies.",
            "Remaining-thickness measurement after the approved corrosion-removal method, as the applicable procedure specifies.",
            _NDT,
        ],
        "severity_factors": [
            "Visible layering or lifting of the surface.",
            "Visible bulging of a lap joint (corrosion may extend out of sight).",
            "A documented location in primary structure or next to a crack.",
            "Recurring corrosion in the same area according to the records.",
        ],
    },
    "other": {"types": [], "inspection_methods": ["Detailed visual inspection; record the finding with its location."], "severity_factors": []},
}

# What "Other" can mean — the reviewer picks one so a reclassified box is never an unexplained "other".
OTHER_TYPES: dict[str, dict] = {
    "staining": {"label": "Stain or discolouration (oil, hydraulic fluid, exhaust, water)",
                 "what_it_is": "Deposits on the surface, such as exhaust soot, oil or hydraulic fluid streaks. A deposit can hide corrosion or indicate a leak.",
                 "next": ["Clean the area if permitted and re-inspect the bare surface", "Trace any fluid streak to its source and record a leak if found",
                          "Check the cleaned surface for corrosion"],
                 "prevent": ["Leaks fixed at the source and deposits washed off on schedule reduce hidden corrosion"]},
    "scratch": {"label": "Scratch, nick or abrasion",
                "what_it_is": "A line of material removal or surface disturbance. It differs from a crack (no separation), but the image alone may not tell them apart.",
                "next": ["Record visible length and location", "Have a qualified person establish whether it is a crack, under the applicable procedure",
                         "Disposition per the applicable SRM/AMM"],
                "prevent": ["Surface protection during maintenance; non-metallic tools for sealant removal"]},
    "coating": {"label": "Paint / coating damage (chips, peeling, blistering)",
                "what_it_is": "Loss or lifting of paint or primer. Exposed metal loses corrosion protection; blistering can indicate corrosion beneath.",
                "next": ["Check the exposed area for corrosion", "Disposition per the applicable finishing procedure"],
                "prevent": ["Early repair of chips under the paint inspection programme"]},
    "fastener": {"label": "Missing, loose or working fastener",
                 "what_it_is": "A rivet or screw that appears missing, loose, or 'working' (dark streaks around the head can indicate movement).",
                 "next": ["Inspect neighbouring fasteners", "Disposition per the applicable SRM/AMM; record why it may have loosened if known"],
                 "prevent": ["Correct installation and fastener-row inspection in high-vibration areas"]},
    "repair": {"label": "Existing repair, doubler, patch or sealant",
               "what_it_is": "A previously repaired or sealed area. Not new damage in itself, but it should match the aircraft's repair records.",
               "next": ["Check the repair against the aircraft records", "Inspect the repair edges as the repair's own instructions require"],
               "prevent": ["Complete repair records and adherence to the repair's inspection requirements"]},
    "no_damage": {"label": "Not damage (reflection, shadow, panel line, normal structure)",
                  "what_it_is": "The model reacted to something that is not a defect, such as glare, a shadow, a panel joint, an antenna or normal skin flexing.",
                  "next": ["Reject the AI box if nothing is wrong", "If unsure, re-photograph with different lighting or angle"],
                  "prevent": ["Even light without direct glare when photographing"]},
    "dirt": {"label": "Dirt, debris or tape on the surface",
             "what_it_is": "Foreign material on the surface, not structural damage. It can hide the surface underneath.",
             "next": ["Clean and re-inspect the surface underneath"], "prevent": ["Clean surfaces before inspection photos"]},
    "unspecified": {"label": "Other (describe in the note)", "what_it_is": "A finding outside the listed types — describe it in the note.",
                    "next": ["Describe and locate the finding", "Disposition per the applicable SRM/AMM"], "prevent": []},
}

# For each class: what it is commonly confused with, what a useful photo shows, and what the image cannot establish.
# ``unknowns`` entries begin with "Not derivable from the image" or "Not recorded in VisionTra" so the two kinds stay distinct.
CAVEATS: dict[str, dict] = {
    "dent": {
        "lookalikes": ["Reflections, glare and specular highlights on polished or wet skin", "Shadows from panel lines, antennas or ground equipment",
                       "Normal flexing of thin skin between stiffeners", "Paint runs, decals and sealant beads", "Lens distortion at the edge of wide-angle phone photos"],
        "evidence_quality": ["Raking (low-angle) light across the surface from more than one direction", "Two angles of the same area, with a scale reference in frame",
                             "Sharp focus, no motion blur; the whole group in view when there is more than one finding"],
        "unknowns": ["Not derivable from the image: depth, length and width (an uncalibrated single photo has no scale or depth information)",
                     "Not derivable from the image: material loss, which a photograph can neither establish nor exclude",
                     "Not derivable from the image: cracking, since paint, the deformation itself or the resolution can hide a crack; no visible crack does not mean no crack",
                     "Not derivable from the image: hidden or subsurface damage to the structure underneath",
                     "Not derivable from the image: the cause of the deformation",
                     "Not recorded in VisionTra: material and thickness of the part",
                     "Not recorded in VisionTra: the applicable aircraft-specific acceptance limits"],
    },
    "crack": {
        "lookalikes": ["Scratches and scribe marks", "Paint or sealant cracking", "Panel joints, lap edges and fastener rows",
                       "Hair, fibres, dirt or fluid streaks", "JPEG compression edges in low-quality images"],
        "evidence_quality": ["Close-up perpendicular to the surface with good, even light", "The nearest fastener row in frame for context and scale",
                             "A clean surface; dirt and fluid trails mimic cracks"],
        "unknowns": ["Not derivable from the image: true length and tip location", "Not derivable from the image: whether it penetrates the material or only the coating",
                     "Not derivable from the image: the cause (fatigue, corrosion, overload)",
                     "Not recorded in VisionTra: whether the location is primary structure", "Not recorded in VisionTra: the applicable aircraft-specific data"],
    },
    "corrosion": {
        "lookalikes": ["Dirt, grease, hydraulic fluid and exhaust staining", "Paint discolouration, primer showing through and touch-up patches",
                       "Rust-coloured sealant or decals", "Shadows inside lap joints"],
        "evidence_quality": ["A cleaned area, photographed before and after loose paint is removed (if permitted)", "Close-up with even light; fastener heads and lap edges in frame",
                             "Any blistering, bulging or powder noted"],
        "unknowns": ["Not derivable from the image: material loss and remaining thickness", "Not derivable from the image: hidden corrosion under laps and fasteners",
                     "Not derivable from the image: corrosion type and depth", "Not recorded in VisionTra: the applicable corrosion programme and limits"],
    },
    "other": {"lookalikes": [], "evidence_quality": ["A clear, well-lit photo with the area's location recorded"],
              "unknowns": ["Not recorded in VisionTra: any measurement or the applicable aircraft-specific data"]},
}
LOW_CONFIDENCE = ("A low confidence score means the model is unsure — not that the defect is small or harmless. Low-confidence candidates "
                  "are shown so they can be checked; reject them if the image does not show the defect, and mark anything the model missed.")


def references(ids: list[str]) -> list[dict]:
    return [SOURCE_BY_ID[i] for i in ids if i in SOURCE_BY_ID]


def for_class(cls: str) -> dict:
    g = GUIDANCE.get(cls) or GUIDANCE["other"]
    c = cls if cls in GUIDANCE else "other"
    return {**g, **CAVEATS[c], **EXPERT[c], "context_indicators": EXPERT[c]["severity_factors"], "low_confidence": LOW_CONFIDENCE, "class": c,
            "disclaimer": DISCLAIMER, "disposition_gate": DISPOSITION_GATE, "sources": references(g["refs"])}


def all_guidance() -> dict:
    return {"classes": {k: for_class(k) for k in GUIDANCE}, "other_types": OTHER_TYPES, "disclaimer": DISCLAIMER, "sources": SOURCES}
