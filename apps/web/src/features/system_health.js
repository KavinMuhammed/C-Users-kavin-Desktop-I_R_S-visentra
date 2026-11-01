/* ==========================================================================
   features/system_health.js — Phase 12: System Health and Diagnostics.
   Every value is read live from the server (health/ready, version, capabilities,
   hardware). Nothing is estimated; a failed check is shown as failed.
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon;
  const tone = (st) => ({ ok: "ok", degraded: "warn", unavailable: "bad", error: "bad" }[st] || "outline");
  const yes = (v, on = "Connected", off = "Not available") => (v ? ui.badge(on, "ok", "check_circle") : ui.badge(off, "outline", "x_circle"));
  const CHECK_LABEL = { database: "Database", object_storage: "Image and report store", model_storage: "Model store", worker: "Background worker", ml_runtime: "ML runtime (PyTorch)" };

  function page() {
    const ready = F.q("sys-ready", () => S.system.ready());
    const ver = F.q("sys-version", () => S.system.version());
    const caps = F.q("sys-caps", () => S.system.capabilities());
    const hw = F.q("sys-hw", () => S.training.hardware());
    const head = ui.pageHeader({ eyebrow: "System", title: "System health", sub: "Live status of the services VisionTra depends on.",
      actions: ui.btn({ label: "Check again", icon: "refresh", act: "sys-recheck" }) });
    const readiness = ui.card({ title: "Service readiness", sub: "GET /api/health/ready", body: ui.q(ready, (h) => {
      const m = lib.status.meta("health", h.status);
      const rows = Object.entries(h.checks || {}).map(([k, c]) => `<tr><th scope="row">${esc(CHECK_LABEL[k] || k)}</th><td>${ui.badge(c.status, tone(c.status), c.status === "ok" ? "check_circle" : "alert")}</td><td class="small muted">${esc([c.detail && typeof c.detail === "object" ? Object.entries(c.detail).map(([k, x]) => `${k} ${x}`).join(", ") : c.detail, c.message, c.mode ? `mode ${c.mode}` : "", c.live_workers != null ? `${c.live_workers} worker${c.live_workers === 1 ? "" : "s"} · ${c.queued_jobs || 0} queued` : "", c.torch ? `PyTorch ${c.torch}` : "", c.cuda === false ? "CPU only" : ""].filter(Boolean).join(" · ") || "—")}${c.latency_ms != null ? ` · ${esc(String(c.latency_ms))} ms` : ""}</td></tr>`).join("");
      return `<div class="col gap-3" data-sys-ready><div class="row gap-2">${ui.badge(m.label, m.tone, m.icon)}<span class="xs muted">checked ${esc(lib.fmt.dateTime(h.checked_at))}</span></div>
        <div class="table-wrap"><table class="tbl"><caption class="sr-only">Dependency checks</caption><thead><tr><th scope="col">Dependency</th><th scope="col">State</th><th scope="col">Detail</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${h.degraded && h.degraded.length ? ui.banner("warn", "alert", `Degraded: ${esc(h.degraded.join(", "))}. Inspections can still be saved while the database and the store are healthy.`) : ""}</div>`;
    }, { skeleton: "card", key: "sys-ready" }) });
    const capsCard = ui.card({ title: "What this server provides", sub: "GET /api/system/capabilities", body: ui.q(caps, (c) => {
      const i = c.inspection || {};
      return `<div data-sys-caps>${ui.kv([
        ["Saved inspections and images", yes(i.persistence)],
        ["Engineer review and sign-off", yes(i.reviews)],
        ["PDF reports", yes(i.reports)],
        ["Defect-detection model", i.detection_model && i.detection_model.available ? `${ui.badge(i.detection_model.model_id, "ok", "check_circle")} <span class="xs muted">${esc((i.detection_model.classes || []).join(", "))} · engineer review required</span>` : `${ui.badge("Not installed", "warn", "alert")} <span class="xs muted">${esc((i.detection_model || {}).reason || "")}</span>`],
        ["Upload limits", i.policy ? esc(`${i.policy.max_upload_mb} MB · ${i.policy.min_side}–${i.policy.max_side} px per side · ${(i.policy.formats || []).map((f) => f.split("/")[1].toUpperCase()).join(", ")}`) : ui.na("—")],
        ["Storage backend", esc((c.storage || {}).backend || "—")],
      ])}</div>`;
    }, { skeleton: "card", key: "sys-caps" }) });
    const verCard = ui.card({ title: "Build", body: ui.q(ver, (v) => ui.kv([["API version", ui.mono(v.api_version)], ["Environment", esc(v.environment)], ["Python", ui.mono(v.python)], ["Build id", v.build ? ui.mono(v.build) : ui.na("not set")], ["Prefixes", esc((v.api_prefixes || []).join(", "))]]), { skeleton: "card", key: "sys-version" }) });
    const hwCard = ui.card({ title: "Compute", sub: "Probed on the server", body: ui.q(hw, (h) => ui.kv([["CPU", esc(h.cpu || "—") + ` <span class="xs muted">${esc(String(h.cpu_count || "?"))} logical cores</span>`], ["CUDA GPU", h.cuda_available ? esc(h.gpu || "available") : "Not available — inference runs on the CPU"], ["Apple MPS", h.mps_available ? "Available" : "Not available"], ["PyTorch", ui.mono(h.torch || "—")]]), { skeleton: "card", key: "sys-hw" }) });
    return `${head}<div class="grid g-2" style="align-items:start">${readiness}${capsCard}${verCard}${hwCard}</div>`;
  }
  lib.on("sys-recheck", () => ["sys-ready", "sys-version", "sys-caps", "sys-hw"].forEach((k) => lib.query.retry(k)));
  lib.router.add("/system", { nav: "system", title: "System health", bg: "low", crumbs: () => ["System health"], view: page });
})(V.features, V.ui, V.lib, V.services);
