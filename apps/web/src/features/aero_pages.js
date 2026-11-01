/* ==========================================================================
   features/aero_pages.js — the inspection product.
   Dashboard (drop an image → it is saved as an inspection and analysed
   automatically), the inspection workspace (detections, engineer decisions,
   sign-off, PDF report), the inspection list, the detection model and the
   datasets it was trained on. Every record is stored on the server.
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon, fmt = lib.fmt, A = S.aero, AV = F.aeroViewer;
  const P = (F.aero = {});

  /* ------------------------------------------------------------------ record cache */
  const recs = new Map(), loading = new Map(), errors = new Map();
  P.put = (insp) => { if (insp && insp.id) { recs.set(insp.id, insp); errors.delete(insp.id); } return insp; };
  P.load = (id, force) => {
    if (loading.has(id)) return loading.get(id);
    if (recs.has(id) && !force) return Promise.resolve(recs.get(id));
    const p = A.get(id).then((x) => { P.put(x); return x; }, (e) => { errors.set(id, e); throw e; })
      .finally(() => { loading.delete(id); lib.store.emit(); });
    loading.set(id, p);
    return p;
  };
  const statusBadge = (s) => { const m = A.STATUS[s] || [s, "outline", "info"]; return ui.badge(m[0], m[1], m[2]); };
  const decBadge = (d) => { const m = A.DECISIONS[d] || A.DECISIONS.pending; return ui.badge(m[0], m[1], m[2]); };
  const chip = (cls, extra = "") => { const d = A.defect(cls); return `<span class="defect-chip" style="--c:${d.hex}"><span class="sw" aria-hidden="true"></span>${esc(d.label)}${extra}</span>`; };
  const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
  const pct1 = (x) => (x == null ? "—" : (x < 0.1 ? (x * 100).toFixed(1) : Math.round(x * 100)) + "%");

  /* Phase 19: plain-language confidence tiers. Each AI box is described as likely / possible / unlikely using the hit rate
     measured on the validation split for that class and score band (models/detection/<id>/display_tiers.json). Display only:
     every stored detection is still listed and must be decided; nothing is deleted or re-thresholded. */
  const TIER = { likely: ["Likely", "tier-likely", "alert"], possible: ["Possible", "tier-possible", "help"], unlikely: ["Unlikely", "tier-unlikely", "info"] };
  const OTHER_SEP = "Other — ";
  const oneIn = (h) => (h >= 0.5 ? `${Math.round(h * 10)} in 10` : `1 in ${Math.max(2, Math.round(1 / Math.max(h, 0.01)))}`);
  /* Aircraft-surface check evidence in plain words. The COCO "closest everyday object" label comes from a general-purpose
     model that has never seen aircraft parts (an engine intake reads as "donut", a radome as "bowl"), so it is shown only as
     a caveated detail, never as the reason. The deciding evidence is named: whole image, a 60 % crop, or the airplane detector. */
  P.gateFacts = (g) => {
    if (!g || g.p_aircraft_surface == null) return "";
    const n = (x) => (x == null ? "–" : Number(x).toFixed(2));
    const t = g.threshold, pm = Math.max(g.p_aircraft_surface || 0, g.p_aircraft_surface_crops || 0);
    const am = Math.max(g.coco_airplane_score || 0, g.coco_airplane_score_crops || 0);
    const what = g.decision === "relevant" ? "aircraft evidence strong enough"
      : g.decision === "uncertain" ? (am >= 0.4 && pm < 0.35 ? "an aircraft shape was found in part of the image, but the surface model was unsure" : "weak aircraft-surface evidence")
      : "no usable aircraft evidence in the whole image or any crop";
    return `<details class="aero-gatefacts"><summary class="xs">Why: ${esc(what)}</summary><dl class="xs mono">
      <dt>Check</dt><dd>${esc(g.gate_id || "gate")}</dd>
      <dt>Aircraft-surface score</dt><dd>whole image ${n(g.p_aircraft_surface)} · best crop ${n(g.p_aircraft_surface_crops)} (relevant at ≥ ${t})</dd>
      <dt>Airplane detector</dt><dd>whole image ${n(g.coco_airplane_score)} · best crop ${n(g.coco_airplane_score_crops)}</dd>
      ${g.coco_top_class ? `<dt>Everyday-object guess</dt><dd>${esc(g.coco_top_class)} ${n(g.coco_top_score)} <span class="muted">— general model, not trained on aircraft parts; often wrong on close-ups. Not used as evidence.</span></dd>` : ""}
    </dl></details>`;
  };
  P.tiers = (insp) => { const m = modelQ().data, a = insp.analysis || {};
    return m && m.display_tiers && (!a.model_id || m.display_tiers.model_id === a.model_id) ? m.display_tiers : null; };
  P.tierOf = (f, tiers) => {
    if (f.kind !== "prediction" || f.score == null) return null;
    const c = tiers && tiers.classes && tiers.classes[f.predicted];
    if (c && c.bands && c.bands.length) {
      const b = c.bands.find((x) => f.score >= x.from - 1e-9 && (x.to == null || f.score < x.to - 1e-9)) || c.bands[c.bands.length - 1];
      return { tier: b.tier, hit: b.hit_rate };
    }
    return { tier: f.low ? "unlikely" : "possible", hit: null };
  };
  const tierPill = (t) => (t ? `<span class="tier-pill ${TIER[t.tier][1]}" title="${t.hit != null ? `On validation images, about ${oneIn(t.hit)} boxes like this were a real defect` : "Measured hit rate not available for this model"}">${esc(TIER[t.tier][0])}</span>` : "");
  const otherType = (f) => (f.cls === "other" && f.comment && f.comment.startsWith(OTHER_SEP) ? f.comment.slice(OTHER_SEP.length) : "");
  const OTHER_TYPES = [["staining", "Stain or discolouration (oil, fluid, exhaust)"], ["scratch", "Scratch, nick or abrasion"], ["coating", "Paint / coating damage"],
    ["fastener", "Missing, loose or working fastener"], ["repair", "Existing repair, patch or sealant"], ["no_damage", "Not damage (glare, shadow, panel line)"],
    ["dirt", "Dirt, debris or tape"], ["unspecified", "Other (describe in the note)"]];
  const modelQ = () => F.q("aero-model", () => A.model());

  /** Detection state (server-derived since Phase 15; derived here for older servers). Never "completed" for a gate rejection. */
  P.detection = (insp) => {
    if (insp && insp.detection) return insp.detection;
    const a = insp && insp.analysis, n = ((insp && insp.predictions) || []).length;
    const st = insp.status === "analysing" ? "analysing" : a && a.out_of_domain && !(a.domain_gate || {}).overridden ? "relevance_rejected"
      : insp.status === "analysis_failed" ? "failed" : !a || insp.analysis_mode === "none" ? "not_analysed" : n ? "completed_candidates" : "completed_zero";
    const label = { analysing: "Detection running", relevance_rejected: "Not analysed: the aircraft-surface check rejected this image", failed: "Detection failed",
      not_analysed: "Not analysed by the detection model", completed_candidates: `${n} AI candidate${n === 1 ? "" : "s"} to review`, completed_zero: "Detection ran: no candidates at the configured thresholds" }[st];
    return { state: st, label, completed: st.startsWith("completed"), gate: a && a.domain_gate, overridden: !!(a && a.domain_gate && a.domain_gate.overridden) };
  };

  /** Detections and engineer findings with their current decision. */
  P.items = (insp) => {
    const cur = insp.current_decisions || {};
    const preds = (insp.predictions || []).map((p) => { const c = cur["prediction:" + p.id] || {};
      return { id: p.id, kind: "prediction", cls: c.corrected_class_id || p.class_id, predicted: p.class_id, score: p.score, low: p.low_confidence, box: p.box, decision: c.decision || "pending", comment: c.comment || "", actor: c.actor, at: c.at }; });
    const hum = (insp.human_findings || []).map((h) => { const c = cur["human_finding:" + h.id] || {};
      return { id: h.id, kind: "human_finding", cls: h.category, score: null, box: h.box, decision: c.decision || "confirmed", comment: c.comment || "", actor: h.created_by, at: h.created_at }; });
    return preds.concat(hum);
  };

  /* ------------------------------------------------------------------ upload → inspection */
  const up = () => lib.viewState("aero-up", { queue: [], fields: { aircraft_ref: "", component: "other", zone: "" }, drag: false });
  const MAX_MB = 5, EXT = /\.(jpe?g|png|webp|bmp|tiff?|gif|heic|heif|avif)$/i;
  let pumping = false;
  P.addFiles = (files) => {
    const v = up(), list = [...files].filter(Boolean);
    if (!list.length) return;
    for (const f of list) {
      const bad = !(f.type.startsWith("image/") || EXT.test(f.name || "")) ? "This is not an image file." : f.size > MAX_MB * 1048576 ? `The image is larger than ${MAX_MB} MB.` : null;
      v.queue.unshift({ key: lib.uid(), file: f, name: f.name || "pasted-image", size: f.size, state: bad ? "error" : "queued", error: bad, single: list.length === 1, idem: crypto.randomUUID ? crypto.randomUUID() : lib.uid() });
    }
    v.queue = v.queue.slice(0, 30);
    lib.store.emit();
    pump();
  };
  async function pump() {
    if (pumping) return; pumping = true;
    try {
      for (;;) {
        const v = up(), job = v.queue.slice().reverse().find((j) => j.state === "queued");
        if (!job) break;
        job.state = "working"; lib.store.emit(); lib.sound.play("scan.start");
        try {
          const insp = P.put(await A.create(job.file, { ...v.fields }, job.idem));
          job.state = "done"; job.insp = { id: insp.id, ref: insp.ref, status: insp.status, n: (insp.predictions || []).length, note: insp.analysis_note || "", ood: P.detection(insp).state === "relevance_rejected" };
          job.file = null;
          lib.invalidate();
          lib.sound.play(job.insp.ood ? "ui.notification" : job.insp.n ? "scan.found" : "scan.clear");
          if (job.single && v.queue.filter((j) => j.state === "queued" || j.state === "working").length === 0 && lib.router.current.path === "/inspection/new") F.go("/inspection/" + insp.id);
        } catch (e) { job.state = "error"; job.error = e.message || "Upload failed."; lib.sound.play("ui.error"); }
        lib.store.emit();
      }
    } finally { pumping = false; }
  }
  lib.on("aero-pick", () => { const i = document.getElementById("aero-file"); i && i.click(); });
  lib.on("aero-files", (el) => { P.addFiles(el.files); el.value = ""; });
  lib.on("aero-field", (el) => { up().fields[el.dataset.k] = el.value; });
  lib.on("aero-up-clear", () => { const v = up(); v.queue = v.queue.filter((j) => j.state === "queued" || j.state === "working"); lib.store.emit(); });
  function mountDrop() {
    const z = document.querySelector("[data-aero-drop]"); if (!z || z._bound) return; z._bound = true;
    const on = (e) => { e.preventDefault(); z.classList.add("over"); }, off = () => z.classList.remove("over");
    z.addEventListener("dragover", on); z.addEventListener("dragenter", on); z.addEventListener("dragleave", off);
    z.addEventListener("drop", (e) => { e.preventDefault(); off(); P.addFiles(e.dataTransfer.files); });
    z.addEventListener("click", (e) => { if (!e.target.closest("button, input, a, select")) lib.dispatch("aero-pick", z); });   // pointer convenience; the button is the keyboard path
  }
  document.addEventListener("paste", (e) => {
    if (!V.app.user || lib.router.current.path !== "/inspection/new" || /INPUT|TEXTAREA/.test((e.target || {}).tagName || "")) return;
    const files = [...(e.clipboardData ? e.clipboardData.files : [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) { e.preventDefault(); P.addFiles(files); }
  });

  function dropzone() {
    const v = up();
    const field = (k, label, ctl) => `<label class="col aero-f" style="gap:4px"><span class="xs muted">${label}</span>${ctl}</label>`;
    const queue = v.queue.length ? `<ul class="aero-queue" aria-live="polite">${v.queue.slice(0, 8).map((j) => `<li data-key="q-${j.key}" class="${j.state}">
        <span class="aq-ic">${j.state === "working" ? '<span class="spin" aria-hidden="true"></span>' : I(j.state === "done" ? "check_circle" : j.state === "error" ? "alert" : "clock")}</span>
        <span class="col" style="gap:1px;min-width:0"><span class="small truncate"><strong>${esc(j.insp ? j.insp.ref : j.name)}</strong>${j.insp ? ` <span class="muted">· ${esc(j.name)}</span>` : ""}</span>
        <span class="xs muted">${j.state === "working" ? "Saving, checking it is an aircraft surface, detecting defects…" : j.state === "queued" ? "Waiting" : j.state === "error" ? esc(j.error) : j.insp.status === "analysis_failed" ? "Saved — detection failed; open it to run again" : j.insp.ood ? "Saved — not an aircraft surface, no detection run" : `${j.insp.n} defect${j.insp.n === 1 ? "" : "s"} detected · awaiting review`}</span></span>
        ${j.insp ? ui.btn({ label: "Open", size: "sm", iconRight: "chev_r", act: "nav", data: { to: "/inspection/" + j.insp.id } }) : ""}</li>`).join("")}</ul>
      ${v.queue.some((j) => j.state === "done" || j.state === "error") ? `<div class="row end">${ui.btn({ label: "Clear finished", size: "sm", variant: "ghost", act: "aero-up-clear" })}</div>` : ""}` : "";
    return `<section class="card aero-inspect" aria-labelledby="aero-in-h">
      <div class="aero-drop" data-aero-drop>
        <div class="aero-drop-art" aria-hidden="true">${lib.aircraft({ size: 132, zones: [] })}</div>
        <div class="col" style="gap:6px;min-width:0">
          <h2 id="aero-in-h">Inspect an aircraft image</h2>
          <p class="muted small" id="aero-drop-hint" style="margin:0">Drop images here, paste, or choose files. Each image is saved as an inspection and checked for cracks, dents and corrosion automatically.</p>
          <div class="row wrap gap-2" style="margin-top:6px">${ui.btn({ label: "Choose images", icon: "upload", variant: "primary", act: "aero-pick", id: "aero-choose" })}<span class="xs muted">Any image format (JPEG, PNG, WebP, HEIC, TIFF, BMP, GIF, AVIF) · up to ${MAX_MB} MB</span></div>
        </div>
        <input type="file" id="aero-file" accept="image/*,.heic,.heif,.avif,.tif,.tiff,.bmp,.gif" multiple hidden data-act="aero-files" data-on="change">
      </div>
      <div class="aero-fields" role="group" aria-label="Optional details applied to the next upload">
        ${field("aircraft_ref", "Aircraft (registration or MSN)", `<input class="input sm" value="${esc(v.fields.aircraft_ref)}" maxlength="64" placeholder="e.g. VT-ANX" data-act="aero-field" data-on="input" data-k="aircraft_ref" aria-label="Aircraft registration or MSN">`)}
        ${field("component", "Component", ui.select({ id: "aero-comp", options: A.COMPONENTS, value: v.fields.component, act: "aero-field", cls: "sm", label: "Component", attrs: 'data-k="component"' }))}
        ${field("zone", "Zone / station", `<input class="input sm" value="${esc(v.fields.zone)}" maxlength="120" placeholder="e.g. STA 540 LH" data-act="aero-field" data-on="input" data-k="zone" aria-label="Zone or station">`)}
      </div>
      ${queue}
    </section>`;
  }

  /* ------------------------------------------------------------------ dashboard */
  function dashboard() {
    const an = F.q("aero-an", () => A.analytics());
    const list = F.q("aero-recent", () => A.list({ per_page: 8 }));
    const mq = modelQ();
    const model = ui.q(mq, (m) => m.available
      ? `<a class="aero-model-chip" href="#/inspection/model" data-act="nav" data-to="/inspection/model">${I("cube")}<span><strong>${esc(m.model_id)}</strong> · ${esc((m.classes || []).join(", "))}</span><span class="xs muted">every detection needs engineer confirmation</span></a>`
      : `<span class="aero-model-chip off">${I("alert")}<span>No detection model installed — images are saved for manual inspection.</span></span>`, { skeleton: "line", key: "aero-model" });
    const kpis = ui.q(an, (a) => {
      const c = a.counts, f = a.findings, conf = Object.values(f.confirmed_by_category || {}).reduce((x, y) => x + y, 0);
      return `<div class="grid g-4 kpis">${[
        ["Inspections", c.inspections, "scan", "/inspection/history"],
        ["Awaiting review", c.awaiting_review + c.in_review, "clock", "/inspection/history?status=open"],
        ["Defects confirmed", conf, "target", "/inspection/history?status=reviewed"],
        ["Signed off", c.reviewed, "check_circle", "/inspection/history?status=reviewed"],
      ].map(([l, n, ic, to]) => ui.metric({ label: l, value: `<span class="num">${fmt.n(n)}</span>`, icon: ic, act: "nav", data: { to } })).join("")}</div>`;
    }, { skeleton: "line", key: "aero-an" });
    const map = fleetMap(an.status === "success" ? an.data : null, an.status === "error" ? an.error : null);
    const attention = ui.q(an, (a) => ui.card({ title: "Needs review", sub: "Not signed off · most pending detections first", pad: false,
      actions: ui.btn({ label: "Defect review", size: "sm", variant: "ghost", iconRight: "chev_r", act: "nav", data: { to: "/inspection/review" } }),
      body: a.attention.length ? `<ul class="insp-recent">${a.attention.slice(0, 5).map((x) => `<li data-key="at-${esc(x.id)}"><a href="#/inspection/${esc(x.id)}" data-act="nav" data-to="/inspection/${esc(x.id)}"><span class="col" style="gap:2px;min-width:0"><span class="row wrap" style="gap:6px"><strong class="small mono">${esc(x.ref)}</strong>${statusBadge(x.status)}</span><span class="xs muted">${esc(A.component(x.component))} · ${esc(fmt.rel(x.updated_at))}</span></span><span class="grow"></span>${x.pending ? `<span class="count warn" title="${x.pending} pending">${x.pending}</span>` : ""}</a></li>`).join("")}</ul>`
        : `<div class="card-b">${ui.empty({ icon: "check_circle", title: "Nothing waiting", text: "Every inspection is signed off." })}</div>` }), { skeleton: "card", key: "aero-an" });
    const recent = ui.card({ title: "Recent inspections", actions: ui.btn({ label: "History", size: "sm", variant: "ghost", iconRight: "chev_r", act: "nav", data: { to: "/inspection/history" } }), pad: false,
      body: ui.q(list, (d) => d.items.length ? rows(d.items, true) : `<div class="card-b">${ui.empty({ icon: "scan", title: "No inspections yet", text: "Start a new inspection to analyse your first aircraft image.", action: ui.btn({ label: "New inspection", icon: "upload", variant: "primary", act: "nav", data: { to: "/inspection/new" } }) })}</div>`, { skeleton: "card", key: "aero-recent" }) });
    const cta = `<section class="card dash-cta"><div class="col gap-3"><div class="eyebrow">${I("scan")} Crack · Dent · Corrosion</div><h2>Precision begins<br> with evidence.</h2>
      <p class="muted small" style="margin:0">Upload photos or drone frames. Each image is checked to be an aircraft surface, analysed for defects and opened for engineer review.</p>
      <div class="row wrap gap-2">${ui.btn({ label: "New inspection", icon: "upload", variant: "primary", size: "lg", act: "nav", data: { to: "/inspection/new" } })}${ui.btn({ label: "Defect review", icon: "check_square", size: "lg", act: "nav", data: { to: "/inspection/review" } })}</div>
      <div class="row wrap gap-2">${model}</div></div></section>`;
    return `<div class="page-h aero-h"><div class="col" style="gap:6px"><div class="eyebrow">01 / VisionTra · Aerospace Forensics</div><h1>Evidence into decisions.</h1><p class="small muted">Your inspection workload. Every finding subject to engineer review.</p></div></div>
      ${kpis}<div class="hangar-hero">${cta}${map}<div class="hangar-caption">REFERENCE AIRFRAME<span>Not a digital twin of the inspected aircraft</span></div></div><div class="grid g-2 dash-lists">${attention}${recent}</div>`;
  }

  /* ------------------------------------------------------------------ new inspection */
  function newInspection() {
    return `${ui.pageHeader({ eyebrow: "New inspection", title: "Upload aircraft images", sub: "Each image becomes an inspection: aircraft-surface check, defect detection, then engineer review." })}${dropzone()}`;
  }

  /* ------------------------------------------------------------------ defect review queue */
  function reviewQueue() {
    const q = F.q("aero-queue", () => Promise.all(["awaiting_review", "in_review", "analysis_failed"].map((st) => A.list({ status: st, per_page: 100 })))
      .then((rs) => rs.flatMap((r) => r.items).sort((a, b) => (b.counts.pending - a.counts.pending) || (a.created_at < b.created_at ? -1 : 1))));
    return `${ui.pageHeader({ eyebrow: "Defect review", title: "Detections waiting for an engineer", sub: "Open an inspection to confirm, reject or reclassify each AI detection and sign it off." })}
      <div class="card" style="padding:0">${ui.q(q, (items) => items.length ? rows(items, true) : `<div class="card-b">${ui.empty({ icon: "check_circle", title: "Review queue is empty", text: "Every inspection is signed off.", action: ui.btn({ label: "New inspection", icon: "upload", variant: "primary", act: "nav", data: { to: "/inspection/new" } }) })}</div>`, { skeleton: "card", key: "aero-queue" })}</div>`;
  }

  /* ------------------------------------------------------------------ reports */
  function reportsPage() {
    const q = F.q("aero-reports", () => A.list({ status: "reviewed", per_page: 100 }));
    const cols = [
      { label: "Inspection", render: (r) => `<strong class="mono small">${esc(r.ref)}</strong> <span class="xs muted">${esc(r.aircraft_ref || "—")} · ${esc(A.component(r.component))}</span>` },
      { label: "Confirmed", render: (r) => (r.categories || []).length ? r.categories.map((c) => chip(c)).join(" ") : '<span class="xs muted">none</span>' },
      { label: "Report", render: (r) => r.has_report ? (r.report_stale ? ui.badge("Out of date", "warn") : ui.badge("PDF available", "ok", "scroll")) : ui.badge("Not generated", "outline") },
      { label: "Signed off", render: (r) => `<span class="xs">${ui.time(r.signed_off_at || r.updated_at)}</span>` },
    ];
    return `${ui.pageHeader({ eyebrow: "Reports", title: "Signed-off inspections", sub: "Open one to generate or download its evidence-linked PDF report." })}
      <div class="card" style="padding:0">${ui.q(q, (d) => ui.table({ rows: d.items, columns: cols, rowAct: "nav", rowData: (r) => ({ to: "/inspection/" + r.id }), caption: "Signed-off inspections",
        empty: `<div class="card-b">${ui.empty({ icon: "scroll", title: "No signed-off inspections yet", text: "Reports are produced after an engineer signs off an inspection." })}</div>` }), { skeleton: "card", key: "aero-reports" })}</div>`;
  }

  /* ------------------------------------------------------------------ analytics */
  function analyticsPage() {
    const an = F.q("aero-an", () => A.analytics());
    return `${ui.pageHeader({ eyebrow: "Analytics", title: "Inspection analytics", sub: "Counted on the server from saved inspections in your workspaces." })}${ui.q(an, (a) => {
      const f = a.findings, c = a.counts, cat = A.DEFECTS.map((d) => ({ name: d.label, count: (f.confirmed_by_category || {})[d.id] || 0, color: d.hex }));   // bars use the class colours of the chips
      const dec = Object.keys(A.DECISIONS).map((k) => ({ name: A.DECISIONS[k][0], count: (f.by_decision || {})[k] || 0 }));
      const comp = Object.entries(a.by_component || {}).map(([k, n]) => ({ name: A.component(k), count: n }));
      const tl = a.timeline || [];
      const kpi = (l, n, ic) => ui.metric({ label: l, value: `<span class="num">${fmt.n(n)}</span>`, icon: ic });
      return `<div class="grid g-4 kpis">${kpi("Inspections", c.inspections, "scan")}${kpi("AI detections", f.ai_predictions, "target")}${kpi("Engineer findings", f.reviewer_findings, "user")}${kpi("Reports", (a.reports || {}).available || 0, "scroll")}</div>
        <div class="grid g-2">
          ${ui.card({ title: "Inspections per day", sub: "Last 30 days", body: tl.some((x) => x.created) ? lib.barChart({ bars: tl.map((x) => ({ label: x.date.slice(5), values: [x.created] })), keys: [{ name: "Inspections", color: "var(--accent)" }], xEvery: 5 }) : ui.empty({ icon: "chart", title: "No inspections in the last 30 days" }) })}
          ${ui.card({ title: "Confirmed defects by type", body: cat.some((x) => x.count) ? ui.hbars(cat, 0) : ui.empty({ icon: "target", title: "No confirmed defects yet" }) })}
          ${ui.card({ title: "Engineer decisions", sub: "AI detections and engineer findings", body: dec.some((x) => x.count) ? ui.hbars(dec, 0) : ui.empty({ icon: "check_square", title: "No decisions yet" }) })}
          ${ui.card({ title: "Inspections by component", body: comp.length ? ui.hbars(comp, 0) : ui.empty({ icon: "layers", title: "No inspections yet" }) })}
        </div>`;
    }, { skeleton: "page", key: "aero-an" })}`;
  }

  /** 3D airframe: a marker on each recorded component with confirmed defects (crimson) or AI detections awaiting review (gold).
      Markers mark the component recorded for an inspection, never a position measured on the aircraft. */
  P.zoneData = (conf = {}, ai = {}) => {
    const z = {};
    for (const [k, n] of Object.entries(conf)) if (n && k !== "other") (z[k] = z[k] || { confirmed: 0, ai: 0 }).confirmed += n;
    for (const [k, n] of Object.entries(ai)) if (n && k !== "other") (z[k] = z[k] || { confirmed: 0, ai: 0 }).ai += n;
    return z;
  };
  function fleetMap(a, err) {
    if (err) return `<div class="ac3d-card is-error">${ui.errorState(err, "aero-an")}</div>`;
    const conf = a ? a.findings.confirmed_by_component || {} : {}, ai = a ? a.findings.ai_predictions_by_component || {} : {};
    const zones = P.zoneData(conf, ai);
    const nConf = Object.values(conf).reduce((x, y) => x + y, 0), nAi = Object.values(ai).reduce((x, y) => x + y, 0);
    const unspec = (conf.other || 0) + (ai.other || 0);
    const hud = !a ? `<span>Loading inspection data…</span>`
      : nConf || nAi ? `<span>Defect map by component</span><span>${fmt.n(nConf)} confirmed · ${fmt.n(nAi)} AI proposals (all review states)${unspec ? ` · ${fmt.n(unspec)} on unspecified components` : ""}</span>`
      : `<span>Inspection component reference</span><span>No defects recorded yet</span>`;
    const label = `3D aircraft. ${Object.entries(zones).map(([z, v]) => `${A.component(z)}: ${v.confirmed} confirmed, ${v.ai} AI proposals (all review states)`).join("; ") || "No defects recorded yet"}`;
    return `<div class="ac3d-card">${lib.ac3dView({ key: "dash", mode: "map", zones, label, hud })}</div>`;
  }

  function rows(items, compact) {
    const cols = [
      { label: "Inspection", render: (r) => `<span class="row" style="gap:10px;min-width:0"><span class="aero-th">${r.thumb_url ? `<img src="${esc(r.thumb_url)}" alt="" loading="lazy" decoding="async">` : I("image")}</span><span class="col" style="gap:1px;min-width:0"><strong class="mono small">${esc(r.ref)}</strong><span class="xs muted truncate">${esc(r.aircraft_ref || "—")} · ${esc(A.component(r.component))}${r.zone ? " · " + esc(r.zone) : ""}</span></span></span>` },
      { label: "AI detections", cls: "num", render: (r) => r.analysis_mode === "model" ? `<span class="num">${fmt.n(r.counts.predictions)}</span>` : '<span class="xs muted">not analysed</span>' },
      { label: "Confirmed", render: (r) => (r.categories || []).length ? r.categories.map((c) => chip(c)).join(" ") : '<span class="xs muted">—</span>' },
      { label: "Status", render: (r) => `${statusBadge(r.status)}${r.counts.pending ? ` <span class="xs muted">${r.counts.pending} pending</span>` : ""}` },
      ...(compact ? [] : [{ label: "Report", render: (r) => r.has_report ? (r.report_stale ? ui.badge("Out of date", "warn") : ui.badge("Available", "ok", "scroll")) : '<span class="xs muted">—</span>' }]),
      { label: "Created", render: (r) => `<span class="xs">${ui.time(r.created_at)}${r.created_by ? `<span class="muted"> · ${esc(r.created_by.name)}</span>` : ""}</span>` },
    ];
    return ui.table({ rows: items, columns: cols, rowAct: "nav", rowData: (r) => ({ to: "/inspection/" + r.id }), caption: "Inspections" });
  }

  /* ------------------------------------------------------------------ inspection list */
  const hv = () => lib.viewState("aero-hist", { q: "", status: "", category: "", page: 1 });
  function history() {
    const v = hv(), qp = lib.router.current.query || {};
    if (qp.status && v._qs !== qp.status) { v._qs = qp.status; v.status = qp.status; v.page = 1; }
    const status = v.status === "open" ? "" : v.status;
    const q = { q: v.q, status, category: v.category, page: v.page, per_page: 25 };
    const key = "aero-list:" + JSON.stringify(q) + (v.status === "open" ? ":open" : "");
    const d = F.q(key, () => A.list(q).then((r) => (v.status === "open" ? { ...r, items: r.items.filter((x) => x.status !== "reviewed") } : r)));
    const filters = `<div class="filterbar">${ui.search({ id: "ah-q", value: v.q, placeholder: "Search reference, aircraft, zone, notes, document text", act: "ah-q" })}
      ${ui.select({ id: "ah-s", options: [["", "Any status"], ["open", "Not signed off"], ["awaiting_review", "Awaiting review"], ["in_review", "In review"], ["analysis_failed", "Analysis failed"], ["reviewed", "Signed off"]], value: v.status, act: "ah-f", cls: "sm", label: "Status", attrs: 'data-k="status"' })}
      ${ui.select({ id: "ah-c", options: [["", "Any confirmed defect"], ...A.DEFECTS.map((x) => [x.id, x.label])], value: v.category, act: "ah-f", cls: "sm", label: "Confirmed defect", attrs: 'data-k="category"' })}</div>`;
    return `${ui.pageHeader({ eyebrow: "History", title: "All inspections", actions: ui.btn({ label: "New inspection", icon: "upload", variant: "primary", act: "nav", data: { to: "/inspection/new" } }) })}
      <div class="card" style="padding:0">${filters}${ui.q(d, (r) => rows(r.items, false) + ui.pager({ page: r.page, per: r.per_page, total: r.total, act: "ah-page" }), { skeleton: "card", key })}</div>`;
  }
  lib.on("ah-q", lib.debounce((el) => { const v = hv(); v.q = el.value; v.page = 1; lib.store.emit(); }, 250));
  lib.on("ah-f", (el) => { const v = hv(); v[el.dataset.k] = el.value; v.page = 1; lib.store.emit(); });
  lib.on("ah-page", (el) => { hv().page = +el.dataset.page; lib.store.emit(); });

  /* ------------------------------------------------------------------ inspection workspace */
  const busy = new Set();
  const wv = (id) => lib.viewState("aero-w:" + id, { note: "" });
  function workspace(p) {
    const id = p.id, insp = recs.get(id);
    if (!insp) {
      if (errors.has(id)) { const e = errors.get(id); return `<div class="card">${ui.empty({ icon: "alert", title: e.status === 404 ? "Inspection not found" : "This inspection could not be loaded", text: esc(e.message || ""), action: (e.status === 404 ? "" : ui.btn({ label: "Try again", icon: "refresh", variant: "primary", act: "aw-retry", data: { id } })) + ui.btn({ label: "History", act: "nav", data: { to: "/inspection/history" } }) })}</div>`; }
      P.load(id).catch(() => {});
      return ui.skeleton("page");
    }
    const tiers = P.tiers(insp), gs = lib.viewState("aero-grp:" + id, { unlikely: false });
    const items = P.items(insp).map((f) => { const t = P.tierOf(f, tiers); return { ...f, tier: t, hidden: !!(t && t.tier === "unlikely" && f.decision !== "confirmed" && !gs.unlikely) }; });
    const v = AV.state(id), perm = insp.review_permissions || {}, canReview = perm.review !== false;
    const preds = items.filter((x) => x.kind === "prediction"), pending = items.filter((x) => x.decision === "pending").length;
    const open = insp.status !== "reviewed", isBusy = busy.has(id), a = insp.analysis;
    const det = P.detection(insp), gateV = det.gate || {}, rejected = det.state === "relevance_rejected";
    const noDecisions = !Object.keys(insp.current_decisions || {}).length;
    const canRun = canReview && open && !rejected && insp.status !== "analysing" && (insp.status === "analysis_failed" || (insp.status === "awaiting_review" && noDecisions));
    const headActs = [
      ui.btn({ label: "Text evidence", icon: "scroll", size: "sm", act: "aw-text", data: { id } }),
      ui.btn({ label: "Download evidence", icon: "download", size: "sm", href: `/api/v1/inspections/${encodeURIComponent(id)}/evidence` }),
      canRun ? ui.btn({ label: det.completed ? "Run detection again" : "Run detection", icon: "scan", size: "sm", act: "aw-analyse", data: { id }, busy: isBusy, disabled: isBusy }) : "",
      (insp.allowed_actions || []).includes("update") ? ui.btn({ label: "Edit details", icon: "edit", size: "sm", variant: "ghost", act: "aw-edit", data: { id } }) : "",
      (insp.allowed_actions || []).includes("delete") ? ui.iconBtn("trash", "Delete inspection", "aw-delete", { id }, { size: "sm" }) : "",
    ].join("");
    const gateFacts = P.gateFacts(gateV);
    const banner = rejected
      ? ui.banner("bad", "alert", `<strong>Not analysed — the aircraft-surface check rejected this image.</strong> The defect detector did not run, so there are no AI results for this image (this is not a zero-defect result).
          The check can misjudge close-ups, radomes, unusual angles and heavy damage; if this is an aircraft surface, run detection with a short reason (recorded under your name).${gateFacts}`,
        canReview && open ? ui.btn({ label: "Run detection anyway", icon: "scan", size: "sm", act: "aw-force", data: { id }, disabled: isBusy }) : "")
      : det.state === "failed"
      ? ui.banner("bad", "alert", `<strong>Detection did not complete</strong>${det.error_code || insp.last_error_code ? ` (${esc(det.error_code || insp.last_error_code)})` : ""}. The image is saved. Run detection again, or record a manual-only inspection.`)
      : det.state === "not_analysed" ? ui.banner("info", "info", "<strong>Not analysed by the detection model.</strong> Run detection, or record findings yourself as a manual-only inspection.")
      : det.state === "completed_zero" ? ui.banner("info", "info", `<strong>Detection ran: no candidates at the configured thresholds.</strong> That is not proof the surface is defect-free — examine the image and mark anything the model missed.
          <span class="xs muted">Known gap: this model misses most dents and hail damage photographed at whole-nose or medium distance (it was trained mostly on panel close-ups).</span>`)
      : "";
    const uncertainNote = det.relevance_uncertain && !rejected ? ui.banner("warn", "alert", `<strong>Relevance uncertain — please confirm this image shows an aircraft surface.</strong> The aircraft-surface check found only weak aircraft evidence, so detection ran and the image was kept for review instead of being discarded. Treat the AI results with extra care.${gateFacts}`) : "";
    const overrideNote = det.overridden && !rejected ? `<div class="aero-override">${I("alert")}<span>Aircraft-surface check overridden${gateV.override_reason ? `: “${esc(gateV.override_reason)}”` : ""}. Results on this image carry that caveat.</span></div>` : "";
    const emptyTitle = { completed_zero: "No AI candidates", relevance_rejected: "No AI results", failed: "No AI results", not_analysed: "No findings yet", analysing: "Detection running" }[det.state] || "No findings yet";
    const emptyText = { completed_zero: "The model returned nothing above its thresholds. Check the image and mark anything it missed.",
      relevance_rejected: "Detection was not run on this image. Mark defects yourself, or run detection anyway.", failed: "Detection failed. Run it again or mark defects yourself.",
      not_analysed: "Mark defects yourself, or run detection." }[det.state] || "";
    const mainRows = items.map((f, k) => [f, k]).filter(([f]) => !(f.tier && f.tier.tier === "unlikely"));
    const lowRows = items.map((f, k) => [f, k]).filter(([f]) => f.tier && f.tier.tier === "unlikely");
    const lowPending = lowRows.filter(([f]) => f.decision === "pending").length;
    const lowGroup = lowRows.length ? `<div class="aero-lowgrp">
        <button type="button" class="aero-lowgrp-h" data-act="aw-grp" data-id="${esc(id)}" aria-expanded="${gs.unlikely}">${I(gs.unlikely ? "chev_d" : "chev_r")}
          <span><strong>${lowRows.length} unlikely candidate${lowRows.length === 1 ? "" : "s"}</strong> — usually false alarms${lowPending ? `, ${lowPending} still to decide` : ""}</span></button>
        ${lowPending && canReview && open ? ui.btn({ label: "Reject unlikely", size: "sm", variant: "ghost", act: "aw-reject-unlikely", data: { id }, disabled: isBusy, title: "Reject every pending unlikely candidate after you have looked at the image" }) : ""}
        ${gs.unlikely ? `<ol class="aero-dets">${lowRows.map(([f, k]) => detRow(insp, f, k, v, canReview && open, isBusy)).join("")}</ol>` : ""}</div>` : "";
    const list = items.length ? `${mainRows.length ? `<ol class="aero-dets">${mainRows.map(([f, k]) => detRow(insp, f, k, v, canReview && open, isBusy)).join("")}</ol>` : `<p class="small muted aero-none">No likely or possible defects. ${lowRows.length ? "Only unlikely candidates were found — open the group below to check them." : ""}</p>`}${lowGroup}`
      : `<div class="card-b">${ui.empty({ icon: det.state === "completed_zero" ? "check_circle" : "target", title: emptyTitle, text: emptyText })}</div>`;
    const byCls = A.DEFECTS.map((d) => [d, preds.filter((x) => x.predicted === d.id).length]).filter(([, n]) => n);
    const detCard = ui.card({ title: `Detections <span class="muted">${items.length}</span>`, sub: preds.length ? byCls.map(([d, n]) => `${n} ${d.label.toLowerCase()}`).join(" · ") + " proposed by the model" : "", pad: false,
      actions: pending ? `${ui.badge(`${pending} to review`, "warn", "clock")}${pending > 1 && canReview && open ? ui.btn({ label: "Reject the rest", size: "sm", variant: "ghost", act: "aw-reject-rest", data: { id }, disabled: isBusy, title: "Mark every pending detection as rejected" }) : ""}` : items.length ? ui.badge("All decided", "ok", "check") : "", body: list });
    const guideCard = guidanceCard(insp, items, v);
    const summary = summaryCard(insp, items, det);
    const done = items.length - items.filter((f) => f.decision === "pending").length;
    const progress = items.length ? `<div class="aero-prog" role="progressbar" aria-label="Review progress" aria-valuemin="0" aria-valuemax="${items.length}" aria-valuenow="${done}"><span style="width:${(done / items.length) * 100}%"></span></div><span class="xs muted">${done} of ${items.length} decided</span>` : "";
    const signCard = open ? ui.card({ title: "Review and sign-off", body: `<div class="col gap-3">${progress ? `<div class="col" style="gap:4px">${progress}</div>` : ""}
        <div class="aero-states"><span>${I("scan")} AI: <strong>${esc(det.label)}</strong></span><span>${I("user")} Review: <strong>${esc((insp.review_state || {}).label || "Not reviewed")}</strong></span></div>
        <p class="small muted" style="margin:0">${pending ? `Decide the ${pending} pending detection${pending === 1 ? "" : "s"} first.` : items.length ? "Every finding has a decision. Signing off records the outcome under your name." : "Signing off records the outcome under your name."}</p>
        ${insp.signoff_requires && !pending ? `<label class="aero-ack"><input type="checkbox" data-act="aw-ack" data-id="${esc(id)}" data-controlled ${wv(id).ack === insp.signoff_requires ? "checked" : ""}>
          <span>${insp.signoff_requires === "manual_only" ? "<strong>Manual-only inspection.</strong> The detection model has not analysed this image; I inspected it myself and recorded what I found." : "<strong>Visual check done.</strong> The model returned no candidates; I examined the image myself and recorded what I found."}</span></label>` : ""}
        <label class="sr-only" for="aw-note">Sign-off note</label><textarea class="input sm" id="aw-note" rows="2" maxlength="600" placeholder="Note (optional)" data-act="aw-note" data-on="input" data-id="${esc(id)}">${esc(wv(id).note)}</textarea>
        ${ui.btn({ label: "Sign off inspection", icon: "check_circle", variant: "primary", act: "aw-signoff", data: { id }, disabled: !!pending || !canReview || isBusy || det.state === "analysing" || (insp.signoff_requires && wv(id).ack !== insp.signoff_requires) })}</div>` })
      : ui.card({ title: "Signed off", actions: ui.badge(insp.signoff_outcome === "findings_recorded" ? "Findings recorded" : "No confirmed findings", insp.signoff_outcome === "findings_recorded" ? "warn" : "ok"), body: `<div class="col gap-3">
        <span class="small">${insp.signed_off_by ? esc(insp.signed_off_by.name) : ""}${insp.signed_off_at ? " · " + esc(fmt.dateTime(insp.signed_off_at)) : ""}</span>${insp.signoff_note ? `<p class="small subtle" style="margin:0">“${esc(insp.signoff_note)}”</p>` : ""}
        ${reportBlock(insp, isBusy)}
        ${canReview ? ui.btn({ label: "Reopen review", icon: "rollback", size: "sm", variant: "ghost", act: "aw-reopen", data: { id }, disabled: isBusy }) : ""}</div>` });
    const m = (insp.media || [])[0] || {};
    const lv = lib.viewState("aero-loc", { open: false });
    const zc = { confirmed: items.filter((f) => f.decision === "confirmed").length, ai: items.filter((f) => f.kind === "prediction" && f.decision === "pending").length };
    const known = insp.component && insp.component !== "other";
    const locCard = ui.card({ title: "Aircraft location", sub: known ? esc(A.component(insp.component)) + (insp.zone ? " · " + esc(insp.zone) : "") : "Component not recorded",
      actions: known ? ui.btn({ label: lv.open ? "Hide 3D" : "Show 3D", icon: "cube", size: "sm", variant: "ghost", act: "aw-loc" }) : "",
      body: !known ? `<p class="small muted" style="margin:0">The component this image shows was not recorded, so it is not placed on the aircraft. Set it with <strong>Edit details</strong>.</p>`
        : lv.open ? lib.ac3dView({ key: "ws", mode: "focus", zones: zc.confirmed || zc.ai ? { [insp.component]: zc } : {}, focus: insp.component, label: `3D aircraft with the ${A.component(insp.component)} highlighted`, cls: "ac3d-compact" })
        : `<p class="small muted" style="margin:0">Recorded component: <strong>${esc(A.component(insp.component))}</strong>. The marker shows the component, not the exact position of a defect — the image above is the evidence.</p>` });
    const facts = ui.card({ title: "Inspection details", body: ui.kv([
      ["Aircraft", esc(insp.aircraft_ref || "—")], ["Component", esc(A.component(insp.component))], ["Zone", esc(insp.zone || "—")], ...(insp.notes ? [["Notes", esc(insp.notes)]] : []),
      ["Image", `${esc(m.filename || "")} <span class="xs muted">${m.width || "?"}×${m.height || "?"} px</span>`], ["SHA-256", `<span class="mono xs" title="${esc(m.sha256 || "")}">${esc((m.sha256 || "").slice(0, 16))}…</span>`],
      ["Detection", a ? `${esc(a.model_id || (a.model && a.model.architecture) || "")} <span class="xs muted">${esc(fmt.dateTime(a.finished_at))}${a.timings_ms && a.timings_ms.total_ms ? ` · ${Math.round(a.timings_ms.total_ms)} ms` : ""}</span>` : '<span class="xs muted">not run</span>'],
      ["AI status", `${esc(det.label)}${det.overridden ? ` <span class="xs muted">(surface check overridden)</span>` : ""}`],
      ...(gateV.p_aircraft_surface != null ? [["Surface check", `${gateV.uncertain ? ui.badge("Uncertain · analysed", "warn", "alert") : gateV.in_domain ? ui.badge("Aircraft surface", "ok", "check") : ui.badge(gateV.overridden ? "Rejected · overridden" : "Rejected", "bad", "x")} <span class="xs muted mono" title="Uncalibrated screening scores. A low surface score alone is not evidence that the image is not an aircraft.">(screening scores: surface ${Number(gateV.p_aircraft_surface).toFixed(3)}, airplane ${gateV.coco_airplane_score != null ? Number(Math.max(gateV.coco_airplane_score, gateV.coco_airplane_score_crops || 0)).toFixed(2) : "–"})</span>`]] : []),
      ["Model", a ? `${esc(a.model_id || "")}${a.model && a.model.architecture ? ` <span class="xs muted">${esc(a.model.architecture)}</span>` : ""}` : '<span class="xs muted">—</span>'],
      ["Created", `${esc(insp.created_by ? insp.created_by.name : "")} · ${ui.time(insp.created_at)}`], ["Updated", ui.time(insp.updated_at)],
      ["Original", m.urls && m.urls.original ? `<a href="${esc(m.urls.original)}" target="_blank" rel="noopener">Open the unmodified upload</a>` : '<span class="xs muted">—</span>']]) });
    return `<div class="page-h workbench-heading"><div class="col" style="gap:8px;min-width:0"><div class="eyebrow">02 / Inspection workbench</div><div class="row wrap gap-2"><h1 class="mono">${esc(insp.ref)}</h1>${statusBadge(insp.status)}</div>
        <p class="muted small" style="margin:0">${esc([insp.aircraft_ref || "Aircraft not specified", insp.component !== "other" ? A.component(insp.component) : "", insp.zone || ""].filter(Boolean).join(" · "))}</p></div><div class="row wrap gap-2">${headActs}</div></div>
      ${banner}${det.screening_failed ? ui.banner("warn", "alert", "<strong>Aircraft-surface screening failed.</strong> Relevance was not established. Confirm this is an aircraft surface before relying on detector results.") : ""}${uncertainNote}${overrideNote}
      <div class="aero-ws"><section class="col gap-3 evidence-column" aria-label="Inspection image and provenance"><div class="evidence-heading"><span class="eyebrow">01 / Visual evidence</span><span class="mono xs">${esc(m.filename || "Source image")}</span></div>${AV.render(insp, items)}<p class="xs muted aero-disclaimer">${I("info")} Boxes are AI proposals. Confidence is an uncalibrated model score, not a measured probability of a defect and not severity. An engineer decides every finding; VisionTra does not determine airworthiness.</p><div class="evidence-context">${facts}${locCard}</div></section>
        <section class="col gap-3 aero-side" aria-label="Findings and engineer review"><div class="evidence-heading"><span class="eyebrow">02 / Engineer review</span><span class="xs muted">Evidence before sign-off</span></div>${detCard}${signCard}<details class="evidence-explainer"><summary>Understand the AI result</summary>${summary}</details>${guideCard}</section></div>`;
  }
  /* Phase 19: the AI result in plain words, separate from the image — one line per defect type the model looks for. */
  function summaryCard(insp, items, det) {
    const analysed = det.completed;
    const rows = ["dent", "crack", "corrosion"].map((c) => {
      const d = A.defect(c), mine = items.filter((f) => f.cls === c);
      const conf = mine.filter((f) => f.decision === "confirmed").length, rej = mine.filter((f) => f.decision === "rejected").length;
      const open = mine.filter((f) => f.decision !== "rejected" && f.decision !== "confirmed" && f.kind === "prediction");
      const order = { likely: 3, possible: 2, unlikely: 1 };
      const best = open.map((f) => f.tier).filter(Boolean).sort((a, b) => order[b.tier] - order[a.tier] || (b.hit || 0) - (a.hit || 0))[0];
      let st, tone, text;
      if (conf) { st = `Confirmed by engineer (${conf})`; tone = "bad"; text = "An engineer confirmed this defect on the image."; }
      else if (best && best.tier !== "unlikely") { st = `${TIER[best.tier][0]} — ${open.length} area${open.length === 1 ? "" : "s"} to check`; tone = best.tier === "likely" ? "bad" : "warn";
        text = best.hit != null ? `On validation images, about ${oneIn(best.hit)} boxes like this were a real ${d.label.toLowerCase()}. An engineer must look before it counts.` : "An engineer must look before it counts."; }
      else if (best) { st = "Unlikely — probably a false alarm"; tone = "muted"; text = best.hit != null ? `Only about ${oneIn(best.hit)} boxes at this low confidence were real on validation images.` : "Low-confidence candidate."; }
      else if (rej && !mine.some((f) => f.decision !== "rejected")) { st = "Rejected by engineer"; tone = "ok"; text = "The AI proposed it; the engineer decided it is not there."; }
      else if (!analysed) { st = det.state === "relevance_rejected" ? "Not analysed" : "Not checked yet"; tone = "muted"; text = "The AI has not analysed this image."; }
      else { st = "Not found by the AI"; tone = "ok"; text = "Nothing above the model's threshold. This is not proof there is none — look at the image."; }
      return `<li class="sum-row t-${tone}"><span class="sum-dot" style="--c:${d.hex}" aria-hidden="true"></span><div class="col" style="gap:2px;min-width:0">
        <span class="row wrap" style="gap:6px"><strong>${esc(d.label)}</strong><span class="sum-st">${esc(st)}</span></span><span class="xs muted">${esc(text)}</span></div></li>`;
    }).join("");
    const others = items.filter((f) => f.cls === "other" && f.decision !== "rejected");
    const otherRows = others.length ? `<li class="sum-row t-muted"><span class="sum-dot" style="--c:${A.defect("other").hex}" aria-hidden="true"></span><div class="col" style="gap:2px">
        <span class="row wrap" style="gap:6px"><strong>Other</strong><span class="sum-st">${others.length} finding${others.length === 1 ? "" : "s"}</span></span>
        <span class="xs muted">${esc(others.map((f) => otherType(f) || "type not specified").join(" · "))}. "Other" means the engineer recorded something that is not a dent, crack or corrosion.</span></div></li>` : "";
    const headline = !analysed ? (det.state === "relevance_rejected" ? "The AI did not analyse this image (it did not look like an aircraft surface)." : det.state === "failed" ? "Detection failed — no AI result." : "No AI result yet.")
      : items.some((f) => f.decision === "confirmed") ? "Defects confirmed by the engineer are listed below."
      : items.some((f) => f.tier && f.tier.tier === "likely" && f.decision === "pending") ? "The AI found a likely defect. An engineer must confirm it."
      : items.some((f) => f.tier && f.tier.tier === "possible" && f.decision === "pending") ? "The AI is unsure: it marked possible areas only. Treat them as places to look, not as findings."
      : "The AI did not find a likely defect on this image.";
    return ui.card({ title: "AI result in plain words", sub: esc(headline), cls: "sum-card", body: `<ul class="sum-list">${rows}${otherRows}</ul>
      <p class="xs muted" style="margin:6px 0 0">The AI looks for three things only: dents, cracks and corrosion. Its percentages are model scores, not probabilities. ${analysed ? "" : ""}</p>` });
  }
  lib.on("aw-grp", (el) => { const g = lib.viewState("aero-grp:" + el.dataset.id, { unlikely: false }); g.unlikely = !g.unlikely; lib.store.emit(); });
  lib.on("aw-reject-unlikely", (el) => {
    const id = el.dataset.id, tiers = P.tiers(recs.get(id));
    const ids = P.items(recs.get(id)).filter((f) => f.kind === "prediction" && f.decision === "pending" && (P.tierOf(f, tiers) || {}).tier === "unlikely").map((f) => f.id);
    if (!ids.length) return;
    ui.confirm({ title: `Reject ${ids.length} unlikely candidate${ids.length === 1 ? "" : "s"}?`, body: "Use this after you have looked at the image. Each rejection is recorded under your name, and the boxes stay in the record.", confirmLabel: "Reject unlikely",
      onConfirm: async () => { const x = await mutate(id, (r) => A.decideBulk(r.id, r.revision, "rejected", ids)); if (x) ui.toast(`${x.decided || ids.length} unlikely candidate${ids.length === 1 ? "" : "s"} rejected`, "ok"); } });
  });

  /* Phase 4: guidance follows the evidence, not the model's guess. A class is "established" only when an engineer confirmed
     a finding of that class (a confirmed AI box, a reclassified box, or an engineer's own finding). While every box of the
     class is a pending or further-review AI proposal the class is provisional: the card shows how to check the proposal,
     never class-specific next steps or repair options as if the defect type were known. */
  P.guidanceBasis = (items, cls, sel) => {
    const basis = sel ? [sel] : items.filter((f) => f.cls === cls && f.decision !== "rejected");
    // a reclassification is stored as decision "confirmed" with the corrected class, so f.cls is already the engineer's class
    return { provisional: !basis.some((f) => f.decision === "confirmed"), further: basis.some((f) => f.decision === "further_review") };
  };
  /* Scientific review 2026-10-29: the finding proposal. The server builds it from stored evidence (inspection/proposal.py);
     this only lays it out. Three stages stay apart and are named in words, never by colour alone: what the AI proposed, what
     the inspector decided, and the engineering disposition, which VisionTra never establishes. */
  const INSPECTOR = { not_reviewed: "Not reviewed yet", rejected: "Rejected by the reviewer", further_review: "Further investigation requested" };
  P.stages = (pr) => {
    const s = pr.stages, i = s.inspector, who = i.by && i.by.name ? ` by ${i.by.name}` : "", L = (c) => A.defect(c).label;
    const human = { confirmed: `Confirmed: ${L(i.class)}${who}`, corrected: `Corrected to ${L(i.class)}${who} (AI: ${L(i.ai_class)})`, recorded_by_reviewer: `${L(i.class)}, recorded${who}` }[i.status];
    return [
      { n: 1, title: "AI proposed finding", state: s.ai ? `${L(s.ai.class)} · model score ${Number(s.ai.score).toFixed(2)}` : "No AI proposal (reviewer's own finding)", tag: s.ai ? "Model output" : "Not applicable", done: !!s.ai },
      { n: 2, title: "Inspector classification", state: human || INSPECTOR[i.status] || i.status, tag: human ? "Human decision" : "Awaiting decision", done: !!human },
      { n: 3, title: "Engineering disposition", state: "Not established", tag: "Outside VisionTra", done: false },
    ];
  };
  P.splitUnknowns = (list) => {
    const out = { image: [], record: [], other: [] };
    for (const u of list || []) {
      const m = /^Not (derivable from the image|recorded in VisionTra): (.*)$/.exec(u);
      if (!m) out.other.push(u); else out[m[1].startsWith("derivable") ? "image" : "record"].push(m[2].charAt(0).toUpperCase() + m[2].slice(1));
    }
    return out;
  };
  const REF_TABS = [["check", "Before you confirm", "eye"], ["types", "Observable features", "layers"], ["context", "Context indicators", "alert"],
    ["inspect", "Further examinations", "scan"], ["cause", "Possible causes", "help"], ["sources", "Sources", "scroll"]];

  function proposalBody(pr) {
    const sec = (t, ic, inner, open = true) => `<details class="prop-sec" ${open ? "open" : ""}><summary>${I(ic)}<span>${t}</span></summary><div class="prop-sec-b">${inner}</div></details>`;
    const ul = (arr) => `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    const stages = `<ol class="prop-stages" aria-label="Review stages of this finding">${P.stages(pr).map((s) => `<li class="prop-stage ${s.done ? "is-done" : "is-open"}">
        <span class="prop-stage-n" aria-hidden="true">${s.n}</span><div class="col" style="gap:1px;min-width:0"><span class="xs muted">${esc(s.title)}</span>
        <strong class="small">${esc(s.state)}</strong><span class="prop-tag">${I(s.done ? "check" : s.n === 3 ? "lock" : "clock")}${esc(s.tag)}</span></div></li>`).join("")}</ol>`;
    const a = pr.assessment, d = pr.stages.disposition, decided = ["confirmed", "corrected"].includes(pr.stages.inspector.status);
    const status = a ? `<p class="small guide-lowconf prop-status st-${decided ? "provisional" : esc(a.status)}" role="note">${I(a.status === "provisional" || decided ? "info" : "alert")}<span><strong>${decided ? "Model output before review: " : ""}${esc(a.status_label)}.</strong> ${
      decided ? "The class shown is the reviewer's; the model's own uncertainty is kept on record below." : a.status === "provisional" ? "The class is the model's proposal, not a verified finding."
        : a.status === "low_score" ? "A low score means the model is unsure — not that the finding is small or harmless."
        : a.status === "ambiguous" ? "Compare the competing classes on the image before deciding." : "Establish that the image is usable before relying on the AI result."}</span></p>` : "";
    const tier = a && a.validation_tier;
    const assess = a ? `${ui.kv([["Predicted class", esc(A.defect(a.predicted_class).label)],
        ["Model score", `<span class="num">${Number(a.score).toFixed(3)}</span> <span class="xs muted">uncalibrated</span>`],
        ...(a.score_threshold != null ? [["Thresholds", `<span class="xs">shown from ${a.score_threshold} · review band from ${a.review_threshold}</span>`]] : []),
        ...(tier ? [["Validation band", `<span class="xs">${esc(TIER[tier.tier] ? TIER[tier.tier][0] : tier.tier)}: ${tier.validation_hit_rate != null ? `${Math.round(tier.validation_hit_rate * 100)}% of ${tier.validation_boxes} validation boxes matched a label` : "hit rate not recorded"}</span>`]] : []),
        ["Competing classes", a.competing.length ? a.competing.map((c) => `${esc(A.defect(c.class).label)} <span class="xs muted">score ${Number(c.score).toFixed(2)}, overlap ${c.overlap_iou}</span>`).join("<br>") : `<span class="xs muted">${esc(a.competing_note)}</span>`],
        ["Model", `<span class="xs mono">${esc(a.model_id || "—")}${a.model_version ? " " + esc(a.model_version) : ""}</span>`]])}
      <details class="guide-more"><summary>What the score and band mean</summary><p class="xs muted" style="margin:0">${esc(a.score_semantics)}</p>${tier ? `<p class="xs muted" style="margin:0">${esc(tier.semantics)}</p>` : ""}${ul(a.limitations)}</details>`
      : `<p class="small muted" style="margin:0">Recorded by a reviewer; no model output belongs to this finding.</p>`;
    const un = P.splitUnknowns(pr.unknowns);
    const unknowns = `${un.image.length ? `<div class="guide-h">${I("camera")}<span>Cannot be derived from this image</span></div>${ul(un.image)}` : ""}
      ${un.record.length ? `<div class="guide-h">${I("help")}<span>Not recorded or measured yet</span></div>${ul(un.record)}` : ""}${un.other.length ? ul(un.other) : ""}`;
    const ctx = ui.kv(pr.identity.context.map((c) => [esc(c.field), c.established ? esc(c.field === "Component" ? A.component(c.value) : c.display) : `<span class="xs muted">${esc(c.display)}</span>`]));
    const disp = `<p class="small" style="margin:0">${esc(d.statement)}</p><ul class="prop-req">${d.requirements.map((r) => `<li class="${r.met ? "is-met" : "is-unmet"}">${I(r.met ? "check" : "x")}<span><strong>${esc(r.item)}</strong> — ${esc(r.state)}</span><span class="sr-only">${r.met ? "met" : "not met"}</span></li>`).join("")}</ul>`;
    return `${stages}${status}
      ${sec("What the record shows", "image", `${ul(pr.observation.statements)}<p class="xs muted" style="margin:0">Basis: ${esc(pr.observation.basis)}</p>`)}
      ${sec("AI assessment", "target", assess)}
      ${sec("What remains unknown", "help", unknowns)}
      ${sec("Evidence to collect next", "check_square", `<ol>${pr.evidence_steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>`)}
      ${sec("Aircraft and location context", "layers", `${ctx}<p class="xs muted" style="margin:0">Possible cause: ${esc(pr.mechanism.statement)}</p>`, false)}
      ${sec("Engineering assessment and disposition", "lock", disp, false)}`;
  }

  function guidanceCard(insp, items, v) {
    const relevant = items.filter((f) => f.decision !== "rejected");
    if (!relevant.length) return "";
    const gv = lib.viewState("aero-guide:" + insp.id, { cls: null, open: true });
    const sel = items.find((f) => f.id === v.sel && f.decision !== "rejected");
    const classes = [...new Set(relevant.map((f) => f.cls))];
    const cls = (sel && sel.cls) || (classes.includes(gv.cls) ? gv.cls : classes[0]);
    const target = sel || relevant.find((f) => f.cls === cls);
    const pr = target && (insp.proposals || {})[target.id];
    const basis = P.guidanceBasis(relevant, cls, sel);
    const q = F.q("aero-guidance", () => A.guidance());
    const ref = ui.q(q, (d) => {
      const g = (d.classes || {})[cls] || (d.classes || {}).other;
      if (!g) return "";
      const list = (t, arr, ic, lead) => `<div class="guide-sec"><div class="guide-h">${I(ic)}<span>${t}</span></div>${lead ? `<p class="xs muted" style="margin:0">${lead}</p>` : ""}<ol>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>`;
      const lowConf = target && target.kind === "prediction" && target.score != null && target.score < 0.25;
      const typed = sel && otherType(sel) ? sel : cls === "other" ? relevant.find((f) => f.cls === "other" && otherType(f)) : null;
      const ot = typed ? Object.values(d.other_types || {}).find((x) => x.label === otherType(typed)) : null;
      const tab = REF_TABS.some(([k]) => k === gv.tab) ? gv.tab : "check";
      const src = (s) => `<li><strong>${esc(s.number || s.title)}</strong> — ${esc(s.org)}${s.revision ? ` · ${esc(s.revision)}` : ""}${s.date ? ` · ${esc(s.date)}` : ""}${s.section ? ` · ${esc(s.section)}` : ""}
          <br><span class="xs muted">Scope: ${esc(s.scope)}</span><br><span class="xs">Supports: ${esc(s.supports)}</span><br>
          <span class="xs muted">${s.role === "background" ? "Background reference" : "Regulatory context"} · checked ${esc(s.verified_on)} · </span><a class="xs" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Official source</a></li>`;
      const pane = ot && tab === "types" ? `<p class="small" style="margin:0"><strong>${esc(ot.label)}.</strong> ${esc(ot.what_it_is)}</p>${list("Next steps", ot.next, "check_square")}`
        : tab === "types" ? list("Features a reviewer may see", g.types || [], "layers", "Record a feature only when the image shows it. Not ranked; none of them sets a severity.")
        : tab === "context" ? list("Context indicators", g.severity_factors || [], "alert", "These may call for more detailed assessment under the applicable instructions. None alone sets a severity or a disposition.")
        : tab === "inspect" ? list("Further examinations", g.inspection_methods || [], "scan")
        : tab === "cause" ? ((g.mechanisms || []).length ? list("Possible causes (background)", g.mechanisms, "help", "A photograph does not establish the cause of this finding.") : `<p class="small muted" style="margin:0">No cause information for this class.</p>`)
        : tab === "sources" ? `<ul class="prop-src">${(g.sources || []).map(src).join("")}</ul><p class="xs muted" style="margin:0">${esc(g.disclaimer)}</p>`
        : `${lowConf ? `<p class="small guide-lowconf">${I("alert")} ${esc(g.low_confidence || "")}</p>` : ""}
          ${(g.lookalikes || []).length ? list("Often confused with", g.lookalikes, "eye") : ""}
          ${(g.evidence_quality || []).length ? list("A useful photo shows", g.evidence_quality, "camera") : ""}`;
      return `<details class="prop-sec" ${gv.ref ? "open" : ""}><summary data-act="aw-guide-ref" data-id="${esc(insp.id)}">${I("scroll")}<span>Class reference · ${esc(A.defect(cls).label)}</span></summary><div class="prop-sec-b">
        <p class="small" style="margin:0">${esc(g.what_it_is)}</p>
        <div class="guide-tabs" role="tablist" aria-label="Class reference">${REF_TABS.map(([k, l, ic]) => `<button type="button" role="tab" id="gt-${k}" aria-selected="${k === tab}" aria-controls="gp-${esc(insp.id)}" class="guide-tab ${k === tab ? "on" : ""}" data-act="aw-guide-tab" data-id="${esc(insp.id)}" data-t="${k}">${I(ic)}<span>${l}</span>${k === "check" && lowConf ? '<i class="dot-warn" aria-label="low confidence"></i>' : ""}</button>`).join("")}</div>
        <div class="guide-pane" role="tabpanel" id="gp-${esc(insp.id)}" aria-labelledby="gt-${tab}">${pane}</div></div></details>`;
    }, { skeleton: "line", key: "aero-guidance" });
    const n = target ? items.indexOf(target) + 1 : null;
    const body = `<div class="col gap-3 prop-card">
        ${classes.length > 1 ? `<div class="row wrap gap-1" role="tablist" aria-label="Defect class">${classes.map((c) => `<button type="button" role="tab" class="chip-btn ${c === cls ? "on" : ""}" aria-selected="${c === cls}" data-act="aw-guide-cls" data-id="${esc(insp.id)}" data-c="${esc(c)}">${chip(c)}</button>`).join("")}</div>` : ""}
        ${pr ? proposalBody(pr) : `<p class="small muted" style="margin:0">The proposal for this finding is not available from this server version.</p>`}
        ${ref}
        <p class="xs muted" style="margin:0">VisionTra does not determine airworthiness, acceptability or repair. Every disposition remains with qualified personnel using the applicable aircraft-specific data.</p></div>`;
    return ui.card({ title: `Finding ${n || ""} proposal · ${esc(A.defect(cls).label)} ${basis.provisional ? "(AI proposal, unconfirmed)" : "(inspector classification)"}`,
      sub: sel ? `Finding ${n} selected` : "Select a finding to see its own proposal", cls: "guide-card",
      actions: ui.btn({ label: gv.open ? "Hide" : "Show", size: "sm", variant: "ghost", act: "aw-guide-toggle", data: { id: insp.id } }), body: gv.open ? body : "" });
  }
  lib.on("aw-guide-ref", (el) => { const g = lib.viewState("aero-guide:" + el.dataset.id, { cls: null, open: true }); g.ref = !g.ref; });
  lib.on("aw-guide-cls", (el) => { lib.viewState("aero-guide:" + el.dataset.id, { cls: null, open: true }).cls = el.dataset.c; const v = AV.state(el.dataset.id); v.sel = null; lib.store.emit(); });
  lib.on("aw-guide-tab", (el) => { lib.viewState("aero-guide:" + el.dataset.id, { cls: null, open: true }).tab = el.dataset.t; lib.store.emit(); });
  lib.on("aw-guide-toggle", (el) => { const g = lib.viewState("aero-guide:" + el.dataset.id, { cls: null, open: true }); g.open = !g.open; lib.store.emit(); });

  function detRow(insp, f, k, v, editable, isBusy) {
    const d = A.defect(f.cls), id = insp.id, sel = v.sel === f.id;
    const reclass = f.kind === "prediction" && f.cls !== f.predicted;
    const quick = editable ? `<span class="row aero-quick" style="gap:2px">${ui.iconBtn("check", `Confirm detection ${k + 1} (C)`, "aw-decide", { id, f: f.id, kind: f.kind, to: "confirmed" }, { size: "sm", disabled: isBusy || f.decision === "confirmed", cls: f.decision === "confirmed" ? "on-ok" : "" })}${ui.iconBtn("x", `Reject detection ${k + 1} (R)`, "aw-decide", { id, f: f.id, kind: f.kind, to: "rejected" }, { size: "sm", disabled: isBusy || f.decision === "rejected", cls: f.decision === "rejected" ? "on-bad" : "" })}</span>` : "";
    const more = editable && sel ? `<div class="row wrap gap-1 aero-acts" role="group" aria-label="More options for detection ${k + 1}">
        ${ui.btn({ label: "Further review", icon: "flag", size: "sm", variant: f.decision === "further_review" ? "primary" : "", act: "aw-decide", data: { id, f: f.id, kind: f.kind, to: "further_review" }, disabled: isBusy || f.decision === "further_review", title: "Further review (F)" })}
        ${f.kind === "prediction" ? ui.select({ id: "rc-" + f.id, options: [["", "Reclassify as…"], ...A.DEFECTS.filter((x) => x.id !== f.predicted && x.id !== "other").map((x) => [x.id, x.label]), ...OTHER_TYPES.map(([k2, l]) => ["other:" + k2, "Other: " + l])], value: "", act: "aw-reclass", cls: "sm", label: `Reclassify detection ${k + 1}`, disabled: isBusy, attrs: `data-id="${esc(id)}" data-f="${esc(f.id)}"` }) : ""}
      </div>` : "";
    return `<li class="aero-det ${sel ? "sel" : ""} dec-${f.decision}" data-det="${esc(f.id)}" data-key="det-${esc(f.id)}" style="--c:${d.hex}">
      <div class="row" style="gap:6px"><button type="button" class="aero-det-h" data-act="aw-focus" data-id="${esc(id)}" data-f="${esc(f.id)}" aria-pressed="${sel}">
        <span class="aero-det-n">${k + 1}</span>${chip(f.cls)}${otherType(f) ? `<span class="xs other-type">${esc(otherType(f))}</span>` : ""}${reclass && !otherType(f) ? `<span class="xs muted">was ${esc(A.defect(f.predicted).label.toLowerCase())}</span>` : ""}<span class="grow"></span>
        ${f.kind === "prediction" ? `${reclass ? "" : tierPill(f.tier)}<span class="num xs muted" title="Model score for '${esc(A.defect(f.predicted).label.toLowerCase())}' — uncalibrated, not a probability">${reclass ? `AI ${esc(A.defect(f.predicted).label.toLowerCase())} ` : ""}${pct(f.score)}</span>` : `<span class="xs muted">engineer</span>`}
        ${decBadge(f.decision)}</button>${quick}</div>
      ${more}
      ${f.comment && !otherType(f) ? `<p class="xs subtle" style="margin:4px 0 0 30px">“${esc(f.comment)}”</p>` : ""}
    </li>`;
  }
  function reportBlock(insp, isBusy) {
    const r = (insp.reports || []).find((x) => x.status === "available");
    const gen = (insp.reports || []).find((x) => x.status === "generating");
    if (gen) return `<span class="small">${I("clock")} Report being generated…</span>`;
    return `<div class="row wrap gap-2">${r && !r.stale ? ui.btn({ label: "Download PDF report", icon: "download", variant: "primary", href: r.download_url }) : ""}
      ${!r || r.stale ? ui.btn({ label: r ? "Generate updated report" : "Generate PDF report", icon: "scroll", variant: r ? "" : "primary", act: "aw-report", data: { id: insp.id }, busy: isBusy, disabled: isBusy }) : ""}
      ${r && r.stale ? ui.btn({ label: "Previous report", icon: "download", size: "sm", variant: "ghost", href: r.download_url }) : ""}</div>`;
  }
  const inflight = new Map();
  async function mutate(id, fn, okMsg) {
    // one write at a time per inspection: a second action waits for the first instead of being dropped
    // (Phase 15 root cause: "Reject the rest" started while a keyboard confirm was in flight and silently did nothing)
    while (inflight.has(id)) { try { await inflight.get(id); } catch {} }
    let done; inflight.set(id, new Promise((r) => (done = r)));
    try { return await mutateNow(id, fn, okMsg); } finally { inflight.delete(id); done(); }
  }
  async function mutateNow(id, fn, okMsg) {
    busy.add(id); lib.store.emit();
    try {
      const r = await fn(recs.get(id));
      const full = r && (r.inspection || (r.id === id ? r : null));
      if (full) P.put(full); else await P.load(id, true);
      if (okMsg) ui.toast(okMsg, "ok");
      lib.invalidate();
      return r;
    } catch (e) {
      if (e.code === "REVIEW_CONFLICT" || e.status === 409 || e.status === 412) { await P.load(id, true).catch(() => {}); }
      ui.toast(e.message || "That didn't work.", "bad");
      return null;
    } finally { busy.delete(id); lib.store.emit(); }
  }
  const findItem = (id, fid) => P.items(recs.get(id)).find((x) => x.id === fid);
  lib.on("aw-focus", (el) => { const v = AV.state(el.dataset.id); v.sel = v.sel === el.dataset.f ? null : el.dataset.f; lib.store.emit(); });
  lib.on("aw-decide", (el) => mutate(el.dataset.id, (r) => A.decide(r.id, r.revision, { target: { type: el.dataset.kind, id: el.dataset.f }, kind: "decision", decision: el.dataset.to }))
    .then((x) => { if (x) { lib.sound.play(el.dataset.to === "confirmed" ? "review.approved" : "ui.click"); nextPending(el.dataset.id, el.dataset.f); } }));
  lib.on("aw-reclass", (el) => { if (!el.value) return; const [to, sub] = el.value.split(":"); const lbl = sub ? (OTHER_TYPES.find((x) => x[0] === sub) || [sub, sub])[1] : "";
    if (sub === "no_damage") return mutate(el.dataset.id, (r) => A.decide(r.id, r.revision, { target: { type: "prediction", id: el.dataset.f }, kind: "decision", decision: "rejected", comment: "Not damage — " + lbl }), "Rejected: not damage");
    mutate(el.dataset.id, (r) => A.decide(r.id, r.revision, { target: { type: "prediction", id: el.dataset.f }, kind: "reclassify", corrected_class_id: to, ...(sub ? { comment: OTHER_SEP + lbl } : {}) }),
      sub ? `Recorded as other: ${lbl.toLowerCase()}` : `Reclassified as ${A.defect(to).label.toLowerCase()}`); });
  function nextPending(id, after) {
    const items = P.items(recs.get(id)), i = items.findIndex((x) => x.id === after);
    const nx = items.slice(i + 1).concat(items.slice(0, i)).find((x) => x.decision === "pending");
    AV.state(id).sel = nx ? nx.id : null; lib.store.emit();
  }
  lib.on("aw-reject-rest", (el) => {
    const id = el.dataset.id, rest = P.items(recs.get(id)).filter((x) => x.decision === "pending");
    ui.confirm({ title: `Reject ${rest.length} pending detection${rest.length === 1 ? "" : "s"}?`, body: "Use this after you have checked the image and confirmed every real defect. Each rejection is recorded under your name.", confirmLabel: "Reject all pending",
      onConfirm: async () => {
        const x = await mutate(id, (r) => A.decideBulk(r.id, r.revision, "rejected", P.items(r).filter((f) => f.kind === "prediction" && f.decision === "pending").map((f) => f.id)));
        if (x) ui.toast(`${x.decided || rest.length} detection${(x.decided || rest.length) === 1 ? "" : "s"} rejected`, "ok");
      } });
  });
  lib.on("aw-note", (el) => { wv(el.dataset.id).note = el.value; });
  lib.on("aw-retry", (el) => { errors.delete(el.dataset.id); P.load(el.dataset.id, true).catch(() => {}); lib.store.emit(); });
  lib.on("aw-loc", () => { const lv = lib.viewState("aero-loc", { open: false }); lv.open = !lv.open; lib.store.emit(); });
  lib.on("aw-ack", (el) => { const r = recs.get(el.dataset.id); wv(el.dataset.id).ack = el.checked && r ? r.signoff_requires : null; lib.store.emit(); });
  lib.on("aw-signoff", (el) => mutate(el.dataset.id, (r) => A.signoff(r.id, r.revision, wv(r.id).note.trim(), r.signoff_requires ? wv(r.id).ack : null), "Inspection signed off").then((x) => { if (x) { wv(el.dataset.id).note = ""; wv(el.dataset.id).ack = null; } }));
  lib.on("aw-reopen", (el) => ui.confirm({ title: "Reopen this review?", body: "The sign-off stays in the history and the current report becomes out of date.", confirmLabel: "Reopen", onConfirm: () => mutate(el.dataset.id, (r) => A.reopen(r.id, r.revision, ""), "Review reopened") }));
  lib.on("aw-report", (el) => mutate(el.dataset.id, (r) => A.report(r.id), "Report generated"));
  lib.on("aw-force", (el) => ui.confirm({ title: "Run defect detection on this image?",
    body: `The aircraft-surface check judged that this image is not an aircraft surface, so detection was not run. Run it only if the image really shows an aircraft surface. The override and your reason are recorded in the audit log.
      <div class="field" style="margin-top:12px"><label for="force-reason">Reason (required)</label><textarea class="input sm" id="force-reason" rows="2" maxlength="300" placeholder="e.g. Close-up of a radome with hail damage"></textarea></div>`,
    confirmLabel: "Run detection", tone: "primary",
    onConfirm: async () => {
      const reason = ((document.getElementById("force-reason") || {}).value || "").trim();
      if (reason.length < 3) throw Object.assign(new Error("Give a short reason (at least 3 characters)."), { code: "VALIDATION" });
      const x = await mutate(el.dataset.id, (r) => A.analyse(r.id, r.revision, true, reason));
      if (x) { const n = (x.predictions || []).length; ui.toast(n ? `${n} AI candidate${n === 1 ? "" : "s"} to review` : "Detection ran: no candidates at the configured thresholds", n ? "ok" : "info"); }
    } }));
  lib.on("aw-analyse", (el) => mutate(el.dataset.id, (r) => A.analyse(r.id, r.revision)).then((x) => { if (!x) return; const d = P.detection(x), n = (x.predictions || []).length;
    ui.toast(d.state === "failed" ? "Detection failed again." : d.state === "relevance_rejected" ? "Not analysed: the aircraft-surface check rejected the image." : n ? `${n} AI candidate${n === 1 ? "" : "s"} to review` : "Detection ran: no candidates at the configured thresholds", d.state === "failed" || d.state === "relevance_rejected" ? "bad" : "ok"); }));
  lib.on("aw-delete", (el) => ui.confirm({ title: "Delete this inspection?", body: "It is removed from lists and kept in the audit trail. This can't be undone from the app.", confirmLabel: "Delete",
    onConfirm: async () => { await A.remove(el.dataset.id); recs.delete(el.dataset.id); lib.invalidate(); F.go("/inspection/history"); ui.toast("Inspection deleted", "ok"); } }));

  /* edit details */
  ui.dialogs["aw-edit"] = ({ id }) => {
    const r = recs.get(id) || {};
    return { title: "Inspection details", sub: esc(r.ref || ""), body: `<form id="aw-edit-form" class="col gap-3" data-act="aw-edit-save" data-id="${esc(id)}" novalidate>
      ${ui.field({ id: "ae-ac", label: "Aircraft (registration or MSN)", control: `<input class="input" id="ae-ac" name="aircraft_ref" maxlength="64" value="${esc(r.aircraft_ref || "")}">` })}
      ${ui.field({ id: "ae-comp", label: "Component", control: ui.select({ id: "ae-comp", name: "component", options: A.COMPONENTS, value: r.component }) })}
      ${ui.field({ id: "ae-zone", label: "Zone / station", control: `<input class="input" id="ae-zone" name="zone" maxlength="120" value="${esc(r.zone || "")}">` })}
      ${ui.field({ id: "ae-notes", label: "Notes", control: `<textarea class="input" id="ae-notes" name="notes" rows="3" maxlength="2000">${esc(r.notes || "")}</textarea>` })}</form>`,
      footer: `${ui.btn({ label: "Cancel", act: "dialog-close" })}${ui.btn({ label: "Save", variant: "primary", type: "submit", icon: "check", form: "aw-edit-form" })}` };
  };
  lib.on("aw-edit", (el) => ui.open("aw-edit", { id: el.dataset.id }));
  lib.on("aw-edit-save", async (form) => {
    const d = lib.formData(form), id = form.dataset.id, r = recs.get(id);
    const body = {}; for (const k of ["aircraft_ref", "component", "zone", "notes"]) if ((d[k] || "") !== (r[k] || "")) body[k] = d[k] || null;
    if (body.component === null) delete body.component;
    if (!Object.keys(body).length) return ui.close();
    const x = await mutate(id, (cur) => A.patch(cur.id, cur.revision, body), "Details saved");
    if (x) ui.close();
  });

  /* Persisted OCR evidence. Each open reloads permissions and server evidence; no cross-workspace cache. */
  async function openText(id) {
    try {
      const insp = await P.load(id, true), evidence = await A.textEvidence(id);
      ui.open("aw-text", { id, insp, items: evidence.items });
    } catch (e) { ui.toast(e.message || "Text evidence could not be loaded. Try again.", "bad"); }
  }
  lib.on("aw-text", (el) => openText(el.dataset.id));
  ui.dialogs["aw-text"] = ({ id, insp, items }) => {
    const editable = insp.status !== "reviewed" && insp.review_permissions?.review === true;
    const original = (insp.allowed_actions || []).includes("read_original");
    return { title: "Text evidence", sub: esc(insp.ref), body: `<p>Extracted text is supporting evidence, not a defect finding. Check it against the source.</p>
      ${insp.status === "reviewed" ? '<p>Signed off: text evidence is read-only. Reopen the review to make corrections.</p>' : ''}
      ${items.length ? items.map((row) => `<section class="col gap-2" style="margin-bottom:24px">
        <h3>${esc(row.filename)}</h3><p class="xs">Status: ${esc(row.status)} · Engine: ${esc(row.engine)} · Document revision: ${esc(row.document_revision || 'Not recorded')}</p>
        <p class="xs mono" style="overflow-wrap:anywhere">SHA-256: ${esc(row.sha256)}</p>
        <p class="xs">Pages: ${esc((row.pages || []).map((p) => `${p.page}: ${p.method}`).join(', ') || 'None')} · Extracted: ${esc(row.created_at)}</p>
        ${original ? `<a href="/api/v1/inspections/${encodeURIComponent(id)}/text-evidence/${encodeURIComponent(row.id)}/source" download>Download source</a>` : ''}
        ${row.error_code ? `<p role="status">Extraction failed: ${esc(row.error_code)}. No extracted text is available.</p>` : ''}
        <h4>Original extracted text</h4><pre style="white-space:pre-wrap;max-height:180px;overflow:auto">${esc(row.original_text || '(No text extracted)')}</pre>
        ${row.corrected_text !== null ? `<h4>Confirmed correction</h4><pre style="white-space:pre-wrap;max-height:180px;overflow:auto">${esc(row.corrected_text)}</pre><p class="xs">Confirmed by ${esc(row.confirmed_by)} at ${esc(row.confirmed_at)}</p>` : ''}
        ${editable && row.status !== 'failed' ? `<form data-act="aw-text-save" data-id="${esc(id)}" data-eid="${esc(row.id)}" data-rev="${row.revision}" class="col gap-2">
          <label for="ocr-${esc(row.id)}">Corrected text</label><textarea class="input" id="ocr-${esc(row.id)}" name="text" rows="5" maxlength="100000">${esc(row.corrected_text ?? row.original_text)}</textarea>
          <label><input type="checkbox" name="confirmed" required> I checked this text against the source.</label>
          ${ui.btn({ label: "Save confirmed correction", type: "submit", variant: "primary" })}</form>` : ''}
      </section>`).join('') : '<p>No text evidence has been saved for this inspection.</p>'}`,
      footer: ui.btn({ label: "Close", act: "dialog-close" }) };
  };
  lib.on("aw-text-save", async (form) => {
    if (!form.reportValidity() || form.dataset.saving) return;
    const data = new FormData(form);
    if (!data.has("confirmed")) return;
    form.dataset.saving = "true";
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    try {
      await A.correctText(form.dataset.id, form.dataset.eid, Number(form.dataset.rev), String(data.get("text") ?? ""));
      await P.load(form.dataset.id, true);
      await openText(form.dataset.id);
      ui.toast("Correction saved. Original extracted text preserved.", "ok");
    } catch (e) {
      ui.toast(e.message || "Correction could not be saved. Your text is still in the form; close and reopen to refresh evidence.", "bad");
    } finally { delete form.dataset.saving; if (button) button.disabled = false; }
  });

  /* mark a missed defect */
  ui.dialogs["aw-mark"] = ({ id, box }) => ({ title: "Record a missed defect", sub: `Box ${fmt.pct(box.w, 0)} × ${fmt.pct(box.h, 0)} of the image`, body: `<form id="aw-mark-form" class="col gap-3" data-act="aw-mark-save" data-id="${esc(id)}" novalidate>
      <fieldset class="col gap-2"><legend class="label">Defect type</legend><div class="cat-pick">${A.DEFECTS.map((d, i) => `<label class="cat-opt" style="--c:${d.hex}"><input type="radio" name="category" value="${d.id}" ${i === 0 ? "checked" : ""}><span class="sw" aria-hidden="true"></span><span class="col" style="gap:0"><strong class="small">${esc(d.label)}</strong><span class="xs muted">${esc(d.desc)}</span></span></label>`).join("")}</div></fieldset>
      ${ui.field({ id: "am-note", label: "Note (optional)", control: `<textarea class="input" id="am-note" name="comment" rows="2" maxlength="600" placeholder="e.g. 40 mm crack aft of fastener row 3"></textarea>` })}</form>`,
    footer: `${ui.btn({ label: "Cancel", act: "dialog-close" })}${ui.btn({ label: "Save finding", variant: "primary", type: "submit", icon: "check", form: "aw-mark-form" })}` });
  lib.on("aw-mark-save", async (form) => {
    const d = lib.formData(form), id = form.dataset.id, props = (lib.ui.state.dialog || {}).props || {};
    const r0 = recs.get(id), m = (r0.media || [])[0];
    const body = { media_id: m.id, category: d.category || "other", box: props.box };
    if ((d.comment || "").trim()) body.comment = d.comment.trim();
    const x = await mutate(id, (r) => A.addFinding(r.id, r.revision, body), "Finding recorded");
    if (x) { AV.state(id).draw = false; ui.close(); }
  });

  function mountWorkspace(p) {
    AV.mount((id, box) => ui.open("aw-mark", { id, box }));
  }
  /* keyboard review: J/K select, C/R/F decide */
  document.addEventListener("keydown", (e) => {
    const cur = lib.router.current; if (!cur.route || cur.route.pattern !== "/inspection/:id" || !V.app.user) return;
    if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.ctrlKey || e.metaKey || e.altKey || lib.ui.state.dialog || lib.ui.state.cmdk) return;
    const id = cur.params.id, insp = recs.get(id); if (!insp) return;
    const items = P.items(insp), v = AV.state(id), i = items.findIndex((x) => x.id === v.sel);
    const k = e.key.toLowerCase();
    if (k === "j" || k === "k") { if (!items.length) return; const n = k === "j" ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1); v.sel = items[n].id; lib.store.emit(); e.preventDefault(); return; }
    const to = { c: "confirmed", r: "rejected", f: "further_review" }[k];
    if (to && i >= 0 && insp.status !== "reviewed" && items[i].decision !== to) { e.preventDefault(); lib.dispatch("aw-decide", { dataset: { id, f: items[i].id, kind: items[i].kind, to } }); }
  });

  /* ------------------------------------------------------------------ detection model */
  function modelPage() {
    return `${ui.pageHeader({ eyebrow: "Defect AI", title: "Detection model", sub: "The models that check and analyse every new inspection.", actions: ui.btn({ label: "Training data", icon: "database", size: "sm", act: "nav", data: { to: "/inspection/datasets" } }) })}${ui.q(modelQ(), (m) => {
      if (!m.available) return ui.card({ body: ui.empty({ icon: "cube", title: "No detection model installed", text: esc(m.reason || "") }) });
      const ev = m.evaluation || {}, per = ev.per_class || {}, th = m.thresholds || {};
      const cls = m.classes || [];
      const metric = (l, v, foot) => ui.metric({ label: l, value: `<span class="num">${v == null ? "—" : fmt.pct(v, 1)}</span>`, foot: foot ? `<span class="xs muted">${foot}</span>` : "" });
      const dt = m.display_tiers;
      const tierCard = dt ? ui.card({ title: "How to read the AI's scores", sub: `Measured on the ${dt.images} validation images: never trained on, but already used to choose the model and its thresholds, so these rates are estimates, not an independent test. A score is not a probability; this table says how often boxes in each band were a real defect.`,
        body: `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Defect</th><th>Score band</th><th>Shown as</th><th class="num">Boxes</th><th class="num">Were real</th></tr></thead><tbody>${Object.entries(dt.classes).map(([c, v]) => v.bands.map((b) =>
          `<tr><td>${chip(c)}</td><td class="num">${pct1(b.from)}${b.to != null ? `–${pct1(b.to)}` : " and up"}</td><td>${tierPill({ tier: b.tier, hit: b.hit_rate })}</td><td class="num">${b.boxes}</td><td class="num">${b.hit_rate != null ? `${Math.round(b.hit_rate * 100)}% (${oneIn(b.hit_rate)})` : "—"}</td></tr>`).join("")).join("")}</tbody></table></div>
          <p class="xs muted" style="margin:8px 0 0">"Likely" needs at least half of the boxes in the band to have been real. Dents and cracks never reach that with this model, so they are shown as "possible" at best.</p>` }) : "";
      // Phase 2 (29 Oct): the test photos are held out by photo group but come from the same aircraft collection as training,
      // so these are not independent validation figures; the per-class table below carries the dent and crack detail.
      return `<div class="grid g-4 kpis">${metric("mAP@50", ev.AP50, "held-out photos · all classes")}${metric("mAP@50–95", ev.AP50_95, "held-out photos · all classes")}${metric("Precision", ev.precision, "all classes, at the thresholds")}${metric("Recall", ev.recall, "all classes, at the thresholds")}</div>
        <p class="xs muted" style="margin:-4px 0 12px">Held out by photo group, not by aircraft: the test photos come from the same aircraft collection as the training photos, and the labels are unverified public annotations. These figures are not an independent validation, a calibration or an accuracy guarantee.</p>
        ${tierCard}
        <div class="grid g-2">
        ${ui.card({ title: esc(m.model_id), sub: `${esc(m.architecture)} · input ${esc((m.input_size || []).join("×"))} px · ${esc(m.readiness)}`, body: ui.kv([
          ["Detects", cls.map((c) => chip(c)).join(" ")], ["Trained on", esc((m.datasets || []).join(", "))], ...(m.training ? [["Training images", fmt.n(m.training.train_images)], ["Validation images", fmt.n(m.training.valid_images)], ["Test images", fmt.n(ev.images)]] : [["Test images", fmt.n(ev.images)]]),
          ["Weights SHA-256", `<span class="mono xs" title="${esc(m.weights_sha256 || "")}">${esc((m.weights_sha256 || "").slice(0, 16))}…</span>`], ["Use", esc(m.policy)]]) })}
        ${ui.card({ title: "Per defect type", sub: "Held-out test split, evaluated once", pad: false, body: `<div class="table-wrap"><table class="tbl stack"><caption class="sr-only">Per-class results</caption><thead><tr><th scope="col">Defect</th><th scope="col" class="num">mAP@50</th><th scope="col" class="num">Precision</th><th scope="col" class="num">Recall</th><th scope="col" class="num">Score threshold</th></tr></thead><tbody>
          ${cls.map((c) => { const x = per[c] || {}; return `<tr><td data-label="Defect">${chip(c)}</td><td data-label="mAP@50" class="num">${pct(x.AP50)}</td><td data-label="Precision" class="num">${pct(x.precision)}</td><td data-label="Recall" class="num">${pct(x.recall)}</td><td data-label="Score threshold" class="num">${th.score_threshold && th.score_threshold[c] != null ? th.score_threshold[c].toFixed(2) : "—"}</td></tr>`; }).join("")}</tbody></table></div>` })}
        </div>
        ${gateCard(m.domain_gate)}
        ${learnCard()}
        ${(m.known_limitations || []).length ? ui.card({ title: "Known limitations", body: `<ul class="aero-lim">${m.known_limitations.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` }) : ""}`;
    }, { skeleton: "page", key: "aero-model" })}`;
  }
  function gateCard(g) {
    if (!g || g.state === "not_installed") return ui.card({ title: "Aircraft-surface check", body: `<p class="small muted" style="margin:0">${esc((g && g.reason) || "Not installed.")} Every image is analysed for defects.</p>` });
    const ev = (g.evaluation || {}).test || {};
    return ui.card({ title: "Aircraft-surface check", sub: `${esc(g.gate_id)} · runs before detection`, body: `<div class="col gap-3">
      <p class="small" style="margin:0">Images that are not an aircraft or aircraft surface — people, animals, vehicles, documents, screenshots — are saved but <strong>not</strong> analysed for defects, so the detector never reports a crack on something that is not an aircraft. An engineer can override it for a specific image; the override is audited.</p>
      ${ev.aircraft_images ? `<div class="grid g-2">${ui.metric({ label: "Aircraft images accepted", value: `<span class="num">${fmt.pct(ev.aircraft_recall, 1)}</span>`, foot: `<span class="xs muted">${ev.aircraft_accepted} of ${ev.aircraft_images} held-out aircraft images</span>` })}${ui.metric({ label: "Other images rejected", value: `<span class="num">${fmt.pct(ev.other_rejection_rate, 1)}</span>`, foot: `<span class="xs muted">${ev.other_rejected} of ${ev.other_images} held-out non-aircraft images</span>` })}</div>` : ""}<p class="xs muted" style="margin:0"><strong>Known limitation (measured outside its test set):</strong> on 18 Oct 2026 the check rejected a real airliner nose photo with hail damage (p = 0.0003; the closest generic object was "donut"). Its test set contains surface close-ups and whole-aircraft shots, not damaged radomes. A rejected image is shown as <em>not analysed</em>, never as defect-free; an engineer can run detection anyway with a recorded reason.</p></div>` });
  }
  function learnCard() {
    const q = F.q("aero-examples", () => A.examples(6));
    return ui.card({ title: "What the model learned from", sub: "Real labelled training images — the boxes are the dataset's own labels",
      actions: ui.btn({ label: "Browse all", size: "sm", variant: "ghost", iconRight: "chev_r", act: "nav", data: { to: "/inspection/datasets" } }), body: ui.q(q, (d) => Object.entries(d.classes).map(([cls, v]) => {
      const def = A.defect(cls);
      return `<div class="learn-head">${chip(cls)}<span class="xs muted">${fmt.n(v.available_images)} training images contain ${esc(def.label.toLowerCase())}</span></div>
        <div class="learn-grid">${v.items.map((it) => `<figure class="learn-tile" style="--c:${def.hex}" data-key="lt-${esc(it.image_id)}"><img src="${esc(it.thumb_url)}" alt="Training image with ${it.n_boxes} labelled ${esc(def.label.toLowerCase())} region${it.n_boxes === 1 ? "" : "s"}" loading="lazy" decoding="async">
          ${it.boxes.map((b) => `<span class="lb" style="left:${(b.x * 100).toFixed(2)}%;top:${(b.y * 100).toFixed(2)}%;width:${(b.w * 100).toFixed(2)}%;height:${(b.h * 100).toFixed(2)}%"></span>`).join("")}
          <figcaption>${esc(it.dataset_id)} · ${it.n_boxes} ${esc(def.label.toLowerCase())}</figcaption></figure>`).join("")}</div>`;
    }).join(""), { skeleton: "card", key: "aero-examples" }) });
  }

  /* ------------------------------------------------------------------ datasets */
  const VID = "VT-PREP-001";
  const dv = () => lib.viewState("aero-ds", { dataset: "", split: "train", page: 1, sel: null });
  function datasetsPage() {
    const q = F.q("aero-ds-list", () => A.datasets.list());
    const v = dv();
    const body = ui.q(q, (d) => {
      const used = d.items.filter((x) => x.locally_available), pending = d.items.filter((x) => !x.locally_available && x.status === "DOWNLOAD_PENDING");
      const other = d.items.length - used.length - pending.length;
      const usedCards = used.map((x) => { const m = x.measured || {};
        return `<article class="card aero-ds"><div class="card-h"><div class="col" style="gap:2px;min-width:0"><h3>${esc(x.name)}</h3><span class="xs muted">${esc(x.id)} · ${esc(x.publisher || "")} · ${esc((x.licence || {}).name || "licence unknown")}</span></div>${ui.badge("In use", "ok", "check")}</div>
          <div class="card-b col gap-2"><div class="row wrap gap-3 small"><span><strong class="num">${fmt.n(m.images)}</strong> images</span><span class="muted">${fmt.n(m.unique_source_images)} unique photos</span></div>
          <div class="row wrap gap-1">${Object.entries(m.instances_by_class || {}).map(([k, n]) => `<span class="xs ds-cls">${esc(k)} <span class="num muted">${fmt.n(n)}</span></span>`).join("")}</div></div></article>`; }).join("");
      const pend = pending.length ? ui.card({ title: "Available to add", sub: "Public aircraft-defect datasets that this server cannot download itself", pad: false, body: `<ul class="aero-dsp">${pending.map((x) => `<li><span class="col" style="gap:1px;min-width:0"><strong class="small">${esc(x.name)}</strong><span class="xs muted">${esc(x.id)} · ${esc((x.source_classes || []).join(", "))} · ${esc((x.licence || {}).name || "")}</span></span>${x.landing_url ? `<a class="btn sm ghost" href="${esc(x.landing_url)}" target="_blank" rel="noopener noreferrer">${I("external")}<span>Source</span></a>` : ""}</li>`).join("")}</ul>
        <div class="card-b"><p class="xs muted" style="margin:0">Download the YOLO export from the source, then run <code>python scripts/datasets/import_dataset.py VT-DS-00N path/to/export.zip</code>. The importer verifies the files, maps the classes to crack, dent and corrosion, and adds the images to the training set for the next model version.</p></div>` }) : "";
      return `<div class="grid g-2">${usedCards}</div>${pend}${other ? `<p class="xs muted">${other} further dataset${other === 1 ? " was" : "s were"} reviewed and excluded (restricted access, wrong surface or unclear licence).</p>` : ""}${browser(v, used)}`;
    }, { skeleton: "page", key: "aero-ds-list" });
    return `${ui.pageHeader({ eyebrow: "Defect AI", title: "Training data", sub: "Labelled aircraft-surface images used to train and test the detection model." })}${body}`;
  }
  function browser(v, used) {
    const qq = { split: v.split, page: v.page, per_page: 24, ...(v.dataset ? { dataset: v.dataset } : {}) };
    const key = "aero-ds-img:" + JSON.stringify(qq);
    const res = F.q(key, () => A.datasets.images(VID, qq));
    const grid = ui.q(res, (d) => d.items.length ? `<ul class="dsl-grid">${d.items.map((it) => `<li><button type="button" class="dsl-tile ${v.sel === it.image_id ? "cur" : ""}" data-act="ads-open" data-id="${esc(it.image_id)}" aria-label="${esc(it.file_name)}, ${it.n_objects} labelled object${it.n_objects === 1 ? "" : "s"}">
        ${it.viewable ? `<img src="${esc(it.thumb_url)}" alt="" loading="lazy" decoding="async" width="160" height="120">` : `<span class="dsl-hidden">${I("lock")}<span class="xs">Held-out test</span></span>`}<span class="dsl-meta"><span class="xs">${it.n_objects} label${it.n_objects === 1 ? "" : "s"}</span></span></button></li>`).join("")}</ul>${ui.pager({ page: d.page, per: d.per_page, total: d.total, act: "ads-page" })}`
      : ui.empty({ icon: "image", title: "No images" }), { skeleton: "card", key });
    const filters = `<div class="row wrap gap-2">${ui.select({ id: "ads-d", options: [["", "All datasets"], ...used.map((x) => [x.id, x.id + " · " + x.name.split(" (")[0]])], value: v.dataset, act: "ads-f", cls: "sm", label: "Dataset", attrs: 'data-k="dataset"' })}${ui.select({ id: "ads-s", options: [["train", "Training"], ["valid", "Validation"], ["test", "Test (held out)"]], value: v.split, act: "ads-f", cls: "sm", label: "Split", attrs: 'data-k="split"' })}</div>`;
    return ui.card({ title: "Images", actions: filters, body: `${v.split === "test" ? ui.banner("info", "lock", "Held-out test images are not displayed, so nothing is tuned on them.") : ""}${grid}${v.sel ? imageDetail(v) : ""}` });
  }
  function imageDetail(v) {
    const q = F.q("aero-ds-one:" + v.sel, () => A.datasets.image(VID, v.sel));
    return `<div class="aero-dsd" data-ads-detail>${ui.q(q, (d) => `<div class="row between"><strong class="small mono truncate">${esc(d.file_name)}</strong>${ui.iconBtn("x", "Close", "ads-close", {}, { size: "sm" })}</div>
      ${d.viewable ? `<div class="dsl-view"><img src="${esc(d.thumb_url)}?size=preview" alt="Dataset image ${esc(d.file_name)} with its labels">${(d.boxes || []).map((b, i) => { const c = b.target_class ? A.defect(b.target_class).hex : "#94A3B8"; return `<span class="dsl-box ${b.target_class ? "mapped" : "unmapped"}" style="--c:${c};left:${b.box.x * 100}%;top:${b.box.y * 100}%;width:${b.box.w * 100}%;height:${b.box.h * 100}%"><span>${esc(b.target_class || b.source_class)}</span></span>`; }).join("")}</div>` : ui.banner("info", "lock", esc(d.not_viewable_reason || "Not displayed."))}
      <p class="xs muted" style="margin:0">${esc(d.dataset_id || "")} · ${esc(d.assigned_split)} · ${(d.boxes || []).length} label${(d.boxes || []).length === 1 ? "" : "s"}${(d.boxes || []).some((b) => !b.target_class) ? " · grey labels are source classes the model does not learn" : ""}</p>`, { skeleton: "card", key: "aero-ds-one:" + v.sel })}</div>`;
  }
  lib.on("ads-f", (el) => { const v = dv(); v[el.dataset.k] = el.value; v.page = 1; v.sel = null; lib.store.emit(); });
  lib.on("ads-page", (el) => { dv().page = +el.dataset.page; lib.store.emit(); });
  lib.on("ads-open", (el) => { dv().sel = el.dataset.id; lib.store.emit(); requestAnimationFrame(() => { const x = document.querySelector("[data-ads-detail]"); x && x.scrollIntoView({ block: "nearest" }); }); });
  lib.on("ads-close", () => { dv().sel = null; lib.store.emit(); });

  /* ------------------------------------------------------------------ routes */
  const C = (t) => () => [["Inspection Center", "/inspection"], t];
  lib.router.add("/inspection", { nav: "dash", title: "Inspection Center", bg: "low", crumbs: () => ["Inspection Center"], view: dashboard });
  lib.router.add("/inspection/new", { nav: "new", title: "New inspection", bg: "low", crumbs: C("New inspection"), view: newInspection, mount: mountDrop });
  lib.router.add("/inspection/review", { nav: "review", title: "Defect review", bg: "low", crumbs: C("Defect review"), view: reviewQueue });
  lib.router.add("/inspection/history", { nav: "list", title: "History", bg: "low", crumbs: C("History"), view: history });
  lib.router.add("/inspection/reports", { nav: "reports", title: "Reports", bg: "low", crumbs: C("Reports"), view: reportsPage });
  lib.router.add("/inspection/analytics", { nav: "analytics", title: "Analytics", bg: "low", crumbs: C("Analytics"), view: analyticsPage });
  lib.router.add("/inspection/model", { nav: "model", title: "Defect AI", bg: "low", crumbs: C("Defect AI"), view: modelPage });
  lib.router.add("/inspection/datasets", { nav: "model", title: "Training data", bg: "low", crumbs: () => [["Defect AI", "/inspection/model"], "Training data"], view: datasetsPage });
  lib.router.add("/inspection/:id", { nav: "list", title: "Inspection", bg: "low", crumbs: (p) => [["History", "/inspection/history"], (recs.get(p.id) || {}).ref || "Inspection"], view: workspace, mount: mountWorkspace });
})(V.features, V.ui, V.lib, V.services);
