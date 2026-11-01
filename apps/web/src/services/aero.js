/* ==========================================================================
   services/aero.js — inspection API client (server records only).
   Every inspection lives on the server: upload → saved → analysed by the
   detection model → reviewed by an engineer → signed off → PDF report.
   ========================================================================== */
(function (S) {
  const R = (...a) => S.request(...a);
  const P = "/api/v1";
  const enc = encodeURIComponent;
  const qs = (o = {}) => { const p = Object.entries(o).filter(([, v]) => v != null && v !== ""); return p.length ? "?" + new URLSearchParams(p).toString() : ""; };
  const ifm = (rev) => (rev == null ? undefined : { "If-Match": `"${rev}"` });

  S.aero = {
    DEFECTS: [
      { id: "crack", label: "Crack", hex: "#FF5C8A", desc: "Crack or skin rupture." },
      { id: "dent", label: "Dent", hex: "#FFB020", desc: "Local deformation of the skin without material loss." },
      { id: "corrosion", label: "Corrosion", hex: "#C084FC", desc: "Surface, pitting or filiform corrosion; paint blistering." },
      { id: "other", label: "Other", hex: "#94A3B8", desc: "Anything else an engineer wants recorded." },
    ],
    COMPONENTS: [["other", "Not specified"], ["fuselage", "Fuselage"], ["wing_upper", "Wing — upper"], ["wing_lower", "Wing — lower"], ["empennage", "Empennage / tail"],
      ["nacelle", "Engine nacelle"], ["control_surface", "Control surface"], ["landing_gear", "Landing-gear bay"]],
    STATUS: {
      created: ["Created", "outline", "clock"], analysing: ["Analysing", "info", "scan"], analysis_failed: ["Analysis failed", "bad", "alert"],
      awaiting_review: ["Awaiting review", "warn", "clock"], in_review: ["In review", "info", "eye"], reviewed: ["Signed off", "ok", "check_circle"],
    },
    DECISIONS: { pending: ["Pending", "warn", "clock"], confirmed: ["Confirmed", "ok", "check"], rejected: ["Rejected", "bad", "x"], further_review: ["Further review", "info", "flag"] },
    defect(id) { return this.DEFECTS.find((d) => d.id === id) || this.DEFECTS[3]; },
    component(id) { return (this.COMPONENTS.find((c) => c[0] === id) || [id, id || "Not specified"])[1]; },

    create(file, fields, key) {
      const fd = new FormData();
      fd.append("file", file, file.name || "image.jpg");
      for (const [k, v] of Object.entries(fields || {})) if (v != null && v !== "") fd.append(k, v);
      fd.append("analyse", "true");
      return R("POST", `${P}/inspections`, { form: fd, idempotent: key || true });
    },
    list: (q) => R("GET", `${P}/inspections${qs(q)}`),
    get: (id) => R("GET", `${P}/inspections/${enc(id)}`),
    patch: (id, rev, body) => R("PATCH", `${P}/inspections/${enc(id)}`, { json: body, headers: ifm(rev) }),
    remove: (id) => R("DELETE", `${P}/inspections/${enc(id)}`),
    analyse: (id, rev, force, reason) => R("POST", `${P}/inspections/${enc(id)}/analyses`, { json: force ? { force: true, reason: reason || "" } : {}, headers: ifm(rev) }),
    examples: (n) => R("GET", `${P}/training-examples${qs({ per_class: n || 6 })}`),
    analyses: (id) => R("GET", `${P}/inspections/${enc(id)}/analyses`),
    textEvidence: (id) => R("GET", `${P}/inspections/${enc(id)}/text-evidence`),
    correctText: (id, eid, rev, text) => R("PATCH", `${P}/inspections/${enc(id)}/text-evidence/${enc(eid)}`, { json: { text, confirmed: true }, headers: ifm(rev) }),
    decide: (id, rev, body) => R("POST", `${P}/inspections/${enc(id)}/decisions`, { json: body, headers: ifm(rev), idempotent: true }),
    addFinding: (id, rev, body) => R("POST", `${P}/inspections/${enc(id)}/findings`, { json: body, headers: ifm(rev), idempotent: true }),
    history: (id) => R("GET", `${P}/inspections/${enc(id)}/decisions`),
    guidance: () => R("GET", `${P}/defect-guidance`),
    decideBulk: (id, rev, decision, targets) => R("POST", `${P}/inspections/${enc(id)}/decisions/bulk`, { json: { decision, targets }, headers: ifm(rev), idempotent: true }),
    signoff: (id, rev, note, acknowledgement) => R("POST", `${P}/inspections/${enc(id)}/signoff`, { json: { ...(note ? { note } : {}), ...(acknowledgement ? { acknowledgement } : {}) }, headers: ifm(rev), idempotent: true }),
    reopen: (id, rev, reason) => R("POST", `${P}/inspections/${enc(id)}/reopen`, { json: reason ? { comment: reason } : {}, headers: ifm(rev), idempotent: true }),
    report: (id) => R("POST", `${P}/inspections/${enc(id)}/reports`, { json: {}, idempotent: true }),
    analytics: () => R("GET", `${P}/inspection-analytics`),
    model: () => R("GET", `${P}/detection-model`),
    datasets: {
      list: () => R("GET", `${P}/detection-datasets`),
      mappings: () => R("GET", `${P}/class-mappings`),
      version: (v) => R("GET", `${P}/detection-dataset-versions/${enc(v)}`),
      images: (v, q) => R("GET", `${P}/detection-dataset-versions/${enc(v)}/images${qs(q)}`),
      image: (v, id) => R("GET", `${P}/detection-dataset-versions/${enc(v)}/images/${enc(id)}`),
    },
  };
})(V.services);
