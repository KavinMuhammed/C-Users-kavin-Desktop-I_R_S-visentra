/* ==========================================================================
   features/admin.js — Audit Log, Settings, search & commands
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon, fmt = lib.fmt, U = lib.ui;

  /* ================= audit ================= */
  const auvs = () => lib.viewState("audit", { project: "", action: "", result: "", from: "", to: "", page: 1 });
  function audit() {
    const v = auvs();
    const params = { project_id: v.project, action: v.action, result: v.result, start: v.from ? new Date(v.from + "T00:00:00").toISOString() : "", end: v.to ? new Date(v.to + "T23:59:59").toISOString() : "", page: v.page, per_page: 50 };
    const key = "audit:" + JSON.stringify(params);
    const e = F.q(key, () => S.audit.list(params));
    const groups = ["auth", "inspection", "review", "report", "member", "workspace"];
    return `${ui.pageHeader({ title: "Audit Log", sub: "Append-only record of significant actions, written in the same transaction as the action itself." })}
      <div class="card"><div class="filterbar">${ui.select({ id: "au-a", options: [["", "Any action"], ...groups.map((g) => [g + ".", g])], value: v.action, act: "au-f", label: "Action", attrs: 'data-f="action"' })}${ui.select({ id: "au-r", options: [["", "Any result"], ["success", "Success"], ["partial", "Partial"], ["failure", "Failure"]], value: v.result, act: "au-f", label: "Result", attrs: 'data-f="result"' })}<input type="date" class="input sm" style="width:auto" aria-label="From" value="${esc(v.from)}" data-act="au-f" data-on="change" data-f="from"><input type="date" class="input sm" style="width:auto" aria-label="To" value="${esc(v.to)}" data-act="au-f" data-on="change" data-f="to"></div>
      ${ui.q(e, (d) => ui.table({ rows: d.items, rowAct: "audit-open", caption: "Audit events", empty: ui.empty({ icon: "scroll", title: auvs().project || auvs().action || auvs().result || auvs().from || auvs().to ? "No events match these filters" : "Important product actions will appear here", text: "Imports, training runs, promotions, reviews and setting changes are recorded as they happen." }), columns: [
        { label: "Time", render: (x) => `<span class="small num" style="white-space:nowrap">${fmt.dateTime(x.at)}</span>` },
        { label: "Actor", render: (x) => esc(x.user_name || "System") },
        { label: "Action", render: (x) => ui.mono(x.action) },
        { label: "Resource", render: (x) => `<span class="small">${esc(x.resource_type)}</span> <span class="mono xs muted">${esc(fmt.short(x.resource_id))}</span>` },
        { label: "Result", render: (x) => x.result === "success" ? ui.badge("Success", "ok") : x.result === "partial" ? ui.badge("Partial", "warn") : ui.badge("Failure", "bad") }] }) + ui.pager({ page: d.page, per: 50, total: d.total, act: "au-page" }) + `<div style="display:none">${(lib.ui.state.auditRows = d.items, "")}</div>`, { skeleton: "table", key })}</div>`;
  }
  lib.on("au-f", (el) => { const v = auvs(); v[el.dataset.f] = el.value; v.page = 1; lib.store.emit(); });
  lib.on("au-page", (el) => { auvs().page = +el.dataset.page; lib.store.emit(); });
  lib.on("audit-open", (el) => { const x = (lib.ui.state.auditRows || []).find((r) => r.id === el.dataset.id); if (x) ui.open("audit-event", { x }); });
  ui.dialogs["audit-event"] = ({ x }) => ({ drawer: true, title: `<span class="mono">${esc(x.action)}</span>`, sub: fmt.dateTime(x.at), body: `${ui.kv([["Event ID", ui.mono(x.id)], ["Timestamp", ui.mono(x.at)], ["Actor", esc(x.user_name || "System")], ["Resource", `${esc(x.resource_type)} <span class="mono xs">${esc(x.resource_id || "—")}</span>`], ["Project", esc(x.project_name || "—")], ["Result", esc(x.result)], ["Request ID", ui.mono(x.request_id || "—")], ["IP", ui.mono(x.ip || "—")]])}<div class="col gap-2"><span class="eyebrow">Metadata</span><pre class="log" style="margin:0">${esc(JSON.stringify(x.meta || {}, null, 2))}</pre></div>`, footer: ui.btn({ label: "Close", act: "dialog-close" }) });

  /* ================= settings ================= */
  const svs = () => lib.viewState("set", { errors: {} });
  const SETTINGS_NAV = [["profile", "Profile", "user"], ["workspace", "Workspace & roles", "users"], ["security", "Security", "lock"], ["appearance", "Appearance & sound", "sun"], ["accessibility", "Accessibility", "eye"]];
  function settings({ section = "profile" }) {
    const me = F.me() || {}, prefs = V.app.prefs || {}, v = svs();
    const nav = SETTINGS_NAV;
    let body;
    if (section === "security") body = `<form class="card" data-act="set-pw" novalidate><div class="card-h"><h3>Change password</h3></div><div class="card-b col gap-4">${ui.field({ id: "pw-cur", label: "Current password", error: v.errors.current, control: ui.input({ id: "pw-cur", name: "current", type: "password", autocomplete: "current-password", error: v.errors.current }) })}${ui.field({ id: "pw-new", label: "New password", hint: "At least 10 characters with a letter and a number. Other sessions will be signed out.", error: v.errors.new, control: ui.input({ id: "pw-new", name: "new", type: "password", autocomplete: "new-password", error: v.errors.new }) })}</div><div class="dialog-f">${ui.btn({ label: "Update password", variant: "primary", type: "submit" })}</div></form>`;
    else if (section === "appearance") body = appearance(prefs);
    else if (section === "accessibility") body = accessibility(prefs);
    else if (section === "workspace") body = workspaceSettings();
    else body = `<form class="card" data-act="set-profile" novalidate><div class="card-h"><h3>Profile</h3></div><div class="card-b col gap-4"><div class="row gap-4">${ui.avatar(me.name, "lg")}<div class="col" style="gap:0"><strong>${esc(me.name)}</strong><span class="small muted">${esc(me.email)} · member since ${fmt.date(me.created_at)}</span></div></div>${ui.field({ id: "pf-name", label: "Full name", error: v.errors.name, control: ui.input({ id: "pf-name", name: "name", value: me.name, autocomplete: "name", error: v.errors.name }) })}</div><div class="dialog-f">${ui.btn({ label: "Save", variant: "primary", type: "submit" })}</div></form>`;
    return `${ui.pageHeader({ title: "Settings" })}<div class="settings-layout"><nav class="settings-nav" aria-label="Settings sections">${nav.map(([id, l, ic]) => `<a class="nav-item" href="#/settings/${id}" data-act="nav" data-to="/settings/${id}" ${id === section ? 'aria-current="page"' : ""}>${I(ic)}<span>${l}</span></a>`).join("")}</nav><div class="col gap-4" style="min-width:0">${body}</div></div>`;
  }
  /* ---------- workspace members & roles (server-enforced RBAC) ---------- */
  const ROLES = [["ADMIN", "Admin — manage members and all inspections"], ["ML_ENGINEER", "ML engineer — datasets and the detection model"], ["OPERATOR", "Operator — upload images and run detection"], ["REVIEWER", "Reviewer — decide detections and sign off"], ["VIEWER", "Viewer — read only"]];
  function workspaceSettings() {
    const w = F.workspace();
    if (!w) return ui.card({ title: "Workspace", body: ui.na("No workspace") });
    const mq = F.q("wsm:" + w.id, () => S.workspaces.members(w.id));
    const manage = (w.permissions || []).includes("workspace.manage");
    return `${ui.card({ title: esc(w.name), sub: w.personal ? "Personal workspace — only you. Create a team workspace from the workspace menu to collaborate." : "Team workspace", actions: ui.badge((w.role || "").replace("_", " "), "info", "shield"), body: ui.kv([["Your permissions", (w.permissions || []).map((x) => `<span class="badge outline mono">${esc(x)}</span>`).join(" ")]]) })}
      ${ui.card({ title: "Members", pad: false, body: ui.q(mq, (ms) => ui.table({ rows: ms, caption: "Workspace members", columns: [{ label: "Name", render: (m) => esc(m.name) }, { label: "Email", render: (m) => esc(m.email) }, { label: "Role", render: (m) => ui.badge(m.role.replace("_", " "), m.role === "OWNER" ? "prod" : "outline") }, { label: "Joined", render: (m) => fmt.date(m.joined_at) }, { label: "", render: (m) => manage && m.role !== "OWNER" ? ui.btn({ label: "Remove", size: "sm", variant: "danger-ghost", act: "wsm-remove", data: { uid: m.user_id, name: m.name } }) : "" }] }), { skeleton: "table" }) })}
      ${manage && !w.personal ? `<form class="card" data-act="wsm-add" novalidate><div class="card-h"><h3>Add or change a member</h3></div><div class="card-b grid g-2">${ui.field({ id: "wsm-email", label: "Email of an existing account", control: ui.input({ id: "wsm-email", name: "email", type: "email", autocomplete: "off" }) })}${ui.field({ id: "wsm-role", label: "Role", control: ui.select({ id: "wsm-role", name: "role", options: ROLES }) })}</div><div class="dialog-f">${ui.btn({ label: "Save member", variant: "primary", type: "submit" })}</div></form>` : ""}`;
  }
  lib.on("wsm-add", async (form) => { const d = lib.formData(form), w = F.workspace(); const r = await F.mutate(form.querySelector("[type=submit]"), () => S.workspaces.setMember(w.id, d.email, d.role), "Member saved"); if (r) form.reset(); });
  lib.on("wsm-remove", (el) => ui.confirm({ title: `Remove ${el.dataset.name}?`, body: "They lose access to every inspection in this workspace.", confirmLabel: "Remove", onConfirm: async () => { await S.workspaces.removeMember(F.workspace().id, el.dataset.uid); lib.invalidate(); } }));
  lib.on("set-profile", async (form) => { const d = lib.formData(form), v = svs(); v.errors = {}; if (!d.name || d.name.trim().length < 2) { v.errors.name = "Enter your name."; return lib.store.emit(); } const r = await F.mutate(form.querySelector("[type=submit]"), () => S.auth.updateProfile(d.name.trim()), "Profile saved"); if (r) V.app.user = r.user; });
  lib.on("set-pw", async (form) => { const d = lib.formData(form), v = svs(); v.errors = {}; try { await S.auth.changePassword(d.current, d.new); form.reset(); ui.toast("Password updated"); } catch (e) { if (e.field) { v.errors[e.field] = e.message; lib.store.emit(); } else ui.handleError(e); } });
  lib.on("set-theme", (el) => { V.app.applyTheme(el.dataset.v); V.app.savePrefs({ theme: el.dataset.v }); });
  lib.on("set-sound", (el) => { V.app.savePrefs({ sound: el.checked }); if (el.checked) setTimeout(() => lib.sound.play("ui.success"), 30); });
  function appearance(prefs) {
    const vol = prefs.volume || {};
    const slider = (ch, label) => `<div class="vol-row"><label for="vol-${ch}">${label}</label><input type="range" id="vol-${ch}" min="0" max="100" step="5" value="${Math.round((vol[ch] == null ? (ch === "master" ? 0.7 : 0.8) : vol[ch]) * 100)}" data-act="set-vol" data-on="change" data-ch="${ch}" ${prefs.sound ? "" : "disabled"}><span class="num xs muted">${Math.round((vol[ch] == null ? (ch === "master" ? 0.7 : 0.8) : vol[ch]) * 100)}%</span>${ui.btn({ icon: "play", title: "Preview", size: "sm", variant: "ghost", act: "set-vol-test", data: { ch }, disabled: !prefs.sound })}</div>`;
    return `${ui.card({ title: "Introduction", sub: "The short VisionTra introduction is shown once in each browser.", body: `<div class="row wrap gap-3" style="align-items:center">${ui.btn({ label: "Replay introduction", icon: "play", act: "intro-replay" })}<span class="xs muted">No sound. Static when reduced motion is on. Esc or Skip closes it.</span></div>` })}
      ${ui.card({ title: "Appearance", body: `<div class="col gap-5">
        <div class="col gap-2"><span class="label">Theme</span>${ui.seg([["dark", "Dark", "moon"], ["light", "Light", "sun"], ["system", "System", "monitor"]], prefs.theme || "dark", "set-theme", "Theme")}</div>
        <div class="col gap-2"><span class="label">Density</span>${ui.seg([["comfortable", "Comfortable"], ["compact", "Compact"]], prefs.density || "comfortable", "set-pref", "Density", 'data-k="density"')}</div>
        <div class="col gap-2"><span class="label">Animation & 3D level</span>${ui.seg([["auto", "Auto"], ["high", "High"], ["balanced", "Balanced"], ["low", "Low"]], prefs.perf || "auto", "set-pref", "Performance mode", 'data-k="perf"')}<span class="xs muted">Auto picks Balanced on machines with ≤4 cores or ≤4 GB memory (currently: ${lib.motion.level()}). Low turns off the background field.</span></div>
        <div class="col gap-2"><span class="label">Background effects</span>${ui.seg([["on", "On"], ["subtle", "Subtle"], ["off", "Off"]], prefs.bgfx || "on", "set-pref", "Background effects", 'data-k="bgfx"')}</div>
        <div class="col gap-2"><span class="label">Accent intensity</span>${ui.seg([["full", "Full"], ["soft", "Soft"]], prefs.accent || "full", "set-pref", "Accent intensity", 'data-k="accent"')}</div></div>` })}
      ${ui.card({ title: "Sound", body: `<div class="col gap-4">${ui.switch({ id: "set-sound", label: "Sound cues", checked: !!prefs.sound, act: "set-sound" })}${ui.switch({ id: "set-uisnd", label: "Subtle click sounds on buttons and navigation", checked: !!prefs.ui_sounds, act: "set-uisnd", disabled: !prefs.sound })}
        <div class="col gap-2">${slider("master", "Master")}${slider("ui", "Interface")}${slider("notifications", "Notifications")}</div>
        <span class="xs muted">Off by default and synthesized in the browser — no audio files. Sounds only play after an action you take; the product is fully usable without them.</span></div>` })}`;
  }
  function accessibility(prefs) {
    return ui.card({ title: "Accessibility", body: `<div class="col gap-5">
      <div class="col gap-2">${ui.switch({ id: "set-rm", label: "Reduce motion (stop 3D animation, entrances and parallax)", checked: prefs.motion === "off", act: "set-rm" })}<span class="xs muted">Your operating system's reduced-motion setting is always respected${matchMedia("(prefers-reduced-motion: reduce)").matches ? " — and it is currently on" : ""}.</span></div>
      ${ui.switch({ id: "set-hc", label: "High contrast (stronger borders and text)", checked: !!prefs.contrast, act: "set-hc" })}
      <div class="col gap-2"><span class="label">Text size</span>${ui.seg([["100", "100%"], ["112", "112%"], ["125", "125%"]], String(prefs.text_scale || 100), "set-pref", "Text size", 'data-k="text_scale"')}</div>
      <div class="col gap-2"><span class="label">Keyboard</span><span class="small muted">Every action is reachable by keyboard. Press <span class="kbd">?</span> for shortcuts and <span class="kbd">Ctrl K</span> for the command palette. Focus is always visible.</span></div></div>` });
  }
  lib.on("set-pref", (el) => { const k = el.dataset.k; let v = el.dataset.v; if (k === "text_scale") v = +v; V.app.savePrefs({ [k]: v }); V.app.applyPrefs(); if (k === "perf") location.reload(); });
  lib.on("set-uisnd", (el) => V.app.savePrefs({ ui_sounds: el.checked }));
  lib.on("set-rm", (el) => { V.app.savePrefs({ motion: el.checked ? "off" : "on" }); V.app.applyPrefs(); });
  lib.on("set-hc", (el) => { V.app.savePrefs({ contrast: el.checked }); V.app.applyPrefs(); });
  lib.on("set-vol", (el) => { const vol = { ...((V.app.prefs || {}).volume || {}) }; vol[el.dataset.ch] = +el.value / 100; V.app.savePrefs({ volume: vol }); });
  lib.on("set-vol-test", (el) => lib.sound.play({ master: "ui.success", ui: "ui.success", notifications: "ui.notification", training: "training.complete" }[el.dataset.ch]));

  /* ================= search & commands ================= */
  let seq = 0;
  const commands = () => [["New inspection", "upload", "/inspection/new"], ["Inspection Center", "scan", "/inspection"], ["Defect review", "check_square", "/inspection/review"], ["History", "clock", "/inspection/history"], ["Reports", "scroll", "/inspection/reports"], ["Analytics", "chart", "/inspection/analytics"],
    ["Defect AI", "cube", "/inspection/model"], ["Training data", "database", "/inspection/datasets"], ["System health", "activity", "/system"], ["Audit log", "scroll", "/audit"], ["Settings", "settings", "/settings"]]
    .map(([t, ic, to]) => ({ type: "Go to", title: t, icon: ic, path: to }));
  F.search = {
    renderPalette() {
      const st = U.state.cmdk; if (!st) return "";
      const q = st.q.trim().toLowerCase();
      const items = commands().filter((c) => !q || c.title.toLowerCase().includes(q)).slice(0, q ? 8 : 12).concat(st.results || []);
      st.flat = items; const idx = Math.min(st.idx, Math.max(0, items.length - 1));
      let html = "", cur = null;
      items.forEach((it, i) => { if (it.type !== cur) { cur = it.type; html += `<div class="cmdk-group">${esc(cur)}</div>`; } html += `<div class="cmdk-item" role="option" id="cmdk-opt-${i}" aria-selected="${i === idx}" data-act="cmdk-run" data-i="${i}">${it.thumb ? `<img src="${esc(it.thumb)}" alt="" style="width:24px;height:24px;border-radius:4px;object-fit:cover">` : I(it.icon || ({ Projects: "folder", Datasets: "database", Models: "cube", Experiments: "layers", Predictions: "target", Reviews: "check_square" }[it.type]) || "search")}<span class="truncate">${esc(it.title)}</span>${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ""}</div>`; });
      return `<div class="overlay" data-act="cmdk-overlay" data-key="cmdk"><div class="cmdk" role="dialog" aria-modal="true" aria-label="Search and commands"><div class="cmdk-in">${I("search", "lg")}<input id="cmdk-input" role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-activedescendant="cmdk-opt-${idx}" placeholder="Search projects, datasets, models, experiments, predictions, reviews…" autocomplete="off" data-act="cmdk-q" data-on="input" value="${esc(st.q)}"><span class="kbd">Esc</span></div><div class="cmdk-list" id="cmdk-list" role="listbox">${items.length ? html : `<div class="state" style="padding:28px"><p>${st.loading ? "Searching…" : st.error ? esc(st.error) : `No results for “${esc(st.q)}”.`}</p></div>`}${st.loading && items.length ? '<div class="cmdk-group">Searching…</div>' : ""}</div><div class="cmdk-foot"><span>↑↓ navigate</span><span>↵ open</span><span>Esc close</span><span class="grow"></span><span>Results come from your projects in the database</span></div></div></div>`;
    },
  };
  const run = lib.debounce(async (q) => { const my = ++seq, st = U.state.cmdk; if (!st) return; if (q.trim().length < 2) { st.results = []; st.loading = false; return lib.store.emit(); } st.loading = true; lib.store.emit(); try { const r = await S.request("GET", "/api/inspections?" + new URLSearchParams({ q: q.trim(), per_page: "8" })); if (my !== seq || !U.state.cmdk) return; U.state.cmdk.results = (r.items || []).map((x) => ({ type: "Inspections", title: `${x.ref} · ${String(x.component || "").replace("_", " ")}${x.aircraft_ref ? " · " + x.aircraft_ref : ""}`, icon: "scan", path: "/inspection/" + x.id })); U.state.cmdk.error = null; } catch (e) { if (U.state.cmdk) U.state.cmdk.error = e.message; } if (U.state.cmdk) U.state.cmdk.loading = false; lib.store.emit(); }, 200);
  lib.on("cmdk-open", () => { U.set({ cmdk: { q: "", idx: 0, results: [] }, popover: null }); requestAnimationFrame(() => { const i = document.getElementById("cmdk-input"); i && i.focus(); }); });
  lib.on("cmdk-close", () => U.set({ cmdk: null }));
  lib.on("cmdk-overlay", (el, ev) => { if (ev.target === el) U.set({ cmdk: null }); });
  lib.on("cmdk-q", (el) => { const st = U.state.cmdk; st.q = el.value; st.idx = 0; lib.store.emit(); run(el.value); });
  const go = (i) => { const st = U.state.cmdk, it = st && st.flat && st.flat[i]; if (!it) return; U.set({ cmdk: null }); F.go(it.path); };
  lib.on("cmdk-run", (el) => go(+el.dataset.i));
  document.addEventListener("keydown", (e) => { const st = U.state.cmdk; if (!st) return; const n = (st.flat || []).length; if (e.key === "ArrowDown") { st.idx = Math.min(n - 1, st.idx + 1); e.preventDefault(); lib.store.emit(); } else if (e.key === "ArrowUp") { st.idx = Math.max(0, st.idx - 1); e.preventDefault(); lib.store.emit(); } else if (e.key === "Enter") { e.preventDefault(); go(Math.min(st.idx, n - 1)); } });

  lib.router.add("/audit", { nav: "audit", title: "Audit Log", bg: "low", crumbs: () => ["Audit Log"], view: audit });
  lib.router.add("/settings", { nav: "settings", title: "Settings", bg: "low", crumbs: () => ["Settings"], view: () => settings({}) });
  lib.router.add("/settings/:section", { nav: "settings", title: "Settings", bg: "low", crumbs: (p) => [["Settings", "/settings"], (SETTINGS_NAV.find((x) => x[0] === p.section) || [0, p.section])[1]], view: settings });
})(V.features, V.ui, V.lib, V.services);
