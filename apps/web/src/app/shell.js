/* ==========================================================================
   app/shell.js — sidebar, header, account and theme menus
   ========================================================================== */
(function (app, F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon, U = lib.ui;
  /* Phase 13: the aircraft-inspection product only. */
  const NAV = [
    { section: "Aircraft inspection", items: [["dash", "Inspection Center", "scan", "/inspection"], ["new", "New Inspection", "upload", "/inspection/new"], ["review", "Defect Review", "check_square", "/inspection/review"]
       ] },
    { section: "Records & reporting", items: [["list", "History", "clock", "/inspection/history"], ["reports", "Reports", "scroll", "/inspection/reports"], ["analytics", "Analytics", "chart", "/inspection/analytics"]] },
    { section: "AI analysis", items: [["model", "Defect AI", "cube", "/inspection/model"]] },
    { section: "System", items: [["system", "System Health", "activity", "/system"], ["audit", "Audit Log", "scroll", "/audit"], ["settings", "Settings", "settings", "/settings"]] },
  ];
  app.NAV = NAV;
  const collapsed = () => !!lib.storage.get("sidebar-collapsed", false);

  /** Apply appearance/accessibility preferences as root attributes (CSS reads them). */
  app.applyPrefs = () => {
    const r = document.documentElement, p = app.prefs || {};
    r.dataset.density = p.density || "comfortable";
    r.dataset.bgfx = p.bgfx || "on";
    r.dataset.accent = p.accent || "full";
    r.toggleAttribute("data-contrast", !!p.contrast);
    r.toggleAttribute("data-reduce-motion", p.motion === "off");
    r.style.fontSize = p.text_scale && p.text_scale !== 100 ? p.text_scale + "%" : "";
  };
  app.applyTheme = (t) => { const r = document.documentElement; if (t === "system") r.removeAttribute("data-theme"); else r.setAttribute("data-theme", t || "dark"); };

  function sidebar(route) {
    const me = F.me() || {};
    const rail = collapsed();
    return `<aside class="sidebar" aria-label="Primary">
      <div class="brand">${F.auth.logo(rail ? 36 : 38)}<div class="col brand-text" style="gap:0"><span class="brand-name">${lib.brandWordmark(13)}</span><span class="brand-sub">Aerospace Forensics</span></div>${ui.iconBtn("x", "Close navigation", "nav-close", null, { cls: "show-md", size: "sm" }).replace('class="btn', 'style="margin-left:auto" class="btn')}</div>
      <button class="ws-switch" data-act="ws-menu" data-menu-anchor aria-haspopup="menu" ${rail ? `data-tip="Workspace"` : ""}><span class="ws-ic">${esc(lib.fmt.initials((F.workspace() || {}).name || me.name || "W"))}</span><span class="col ws-text" style="gap:0;min-width:0"><span class="xs muted">Workspace</span><span class="truncate small" style="font-weight:600">${esc((F.workspace() || {}).name || "Workspace")}</span></span>${I("chev_ud")}</button>
      <nav class="nav">${NAV.map((sec) => `<div class="nav-sec">${sec.section}</div>${sec.items.map(([id, label, icon, path]) => `<a class="nav-item" href="#${path}" data-act="nav" data-to="${path}" ${rail ? `data-tip="${label}"` : ""} ${route && route.nav === id ? 'aria-current="page"' : ""}>${I(icon)}<span class="nav-label">${label}</span></a>`).join("")}`).join("")}</nav>
      <div class="side-foot"><div class="row between xs muted"><span class="side-foot-text">Decision support · engineer sign-off required</span>
      <button class="btn ghost icon sm collapse-btn hide-md" data-act="side-collapse" aria-label="${rail ? "Expand" : "Collapse"} sidebar" data-tip="${rail ? "Expand" : "Collapse"} sidebar">${I("panel")}</button></div></div></aside>`;
  }

  function header(route, params) {
    const me = F.me() || {};
    let crumbs = [];
    try { crumbs = route && route.crumbs ? route.crumbs(params) : [route ? route.title : "Not found"]; } catch { crumbs = [route ? route.title : ""]; }
    return `<header class="header">
      ${ui.iconBtn("menu", "Open navigation", "nav-open", null, { cls: "hamburger" })}
      <nav class="crumbs grow" aria-label="Breadcrumb">${crumbs.map((c, i) => { const last = i === crumbs.length - 1; const [label, to] = Array.isArray(c) ? c : [c]; return `${i ? '<span class="sep" aria-hidden="true">/</span>' : ""}${last ? `<span class="cur" aria-current="page">${esc(label)}</span>` : to ? `<a href="#${to}" data-act="nav" data-to="${to}">${esc(label)}</a>` : `<span>${esc(label)}</span>`}`; }).join("")}</nav>
      <button class="search-trigger" data-act="cmdk-open" aria-label="Search (Ctrl+K)">${I("search")}<span class="lbl">Search…</span><span class="kbd">${/Mac/.test(navigator.platform) ? "⌘" : "Ctrl"} K</span></button>
      <div class="hdr-actions">
        ${ui.iconBtn(app.prefs && app.prefs.sound ? "volume" : "volume_off", app.prefs && app.prefs.sound ? "Sound effects on" : "Sound effects off", "toggle-sound", null, { cls: "snd-toggle hide-sm" }).replace("<button", `<button aria-pressed="${!!(app.prefs && app.prefs.sound)}"`)}
        ${ui.iconBtn("play", "Replay the introduction", "intro-replay", null, { cls: "hide-sm" })}
        ${ui.iconBtn("help", "Keyboard shortcuts", "help-open", null, { cls: "hide-sm" })}
        ${ui.iconBtn((app.prefs && app.prefs.theme) === "light" ? "sun" : (app.prefs && app.prefs.theme) === "system" ? "monitor" : "moon", "Theme", "theme-menu").replace("<button", "<button data-menu-anchor")}
        <button class="btn ghost icon" data-act="account-menu" data-menu-anchor aria-haspopup="menu" aria-label="Account menu">${ui.avatar(me.name)}</button>
      </div></header>`;
  }

  function staleBanner() {
    const st = app.serverStale; if (!st) return "";
    if (st.pageOld) return `<div class="stale-banner" role="alert" data-key="stale">${I("refresh")}<div class="col" style="gap:4px"><strong>VisionTra was updated.</strong>
      <span class="small">This tab still shows the previous version. Reload to use the new one — your inspections are saved on the server.</span>
      <span class="xs muted mono">page build ${esc(st.page)} · server build ${esc(String(st.server))}</span></div>${ui.btn({ label: "Reload now", icon: "refresh", size: "sm", variant: "primary", act: "stale-reload" })}</div>`;
    return `<div class="stale-banner" role="alert" data-key="stale">${I("alert")}<div class="col" style="gap:4px"><strong>The VisionTra server is running an older version than this page.</strong>
      <span class="small">The server process was started before the latest update, so some data cannot load. Stop the server (Ctrl+C in its window) and start it again — or run <code>start-visiontra.bat</code> in the VisionTra folder — then reload this page.</span>
      <span class="xs muted mono">page build ${esc(st.page)} · server build ${esc(String(st.server))}${st.started ? ` · server started ${esc(st.started)}` : ""}</span></div>${ui.btn({ label: "Check again", icon: "refresh", size: "sm", act: "stale-recheck" })}</div>`;
  }
  lib.on("stale-recheck", () => app.checkServerVersion && app.checkServerVersion());
  lib.on("stale-reload", () => location.reload());
  app.renderShell = (route, params, content) => `<a href="#main" class="sr-only" data-act="skip-main">Skip to content</a><div class="app ${U.state.navOpen ? "nav-open" : ""} ${collapsed() ? "side-collapsed" : ""}">${sidebar(route)}<div class="scrim" data-act="nav-close"></div><div class="main-col"><canvas class="ambient" data-fx="ambient" data-intensity="${(route && route.bg) || "medium"}" aria-hidden="true"></canvas>${header(route, params)}<div class="content" id="content" tabindex="0" role="region" aria-label="Page content"><div class="spot" aria-hidden="true"></div><main id="main" data-key="pg:${esc(lib.router.current.path)}" tabindex="-1" class="page">${staleBanner()}${content}</main></div></div></div>`;

  lib.on("skip-main", () => { const m = document.getElementById("main"); m && m.focus(); });
  lib.on("nav-open", () => U.set({ navOpen: true }));
  lib.on("nav-close", () => U.set({ navOpen: false }));
  const savePrefs = (patch) => { app.prefs = { ...(app.prefs || {}), ...patch }; lib.store.emit(); S.auth.savePreferences(patch).catch(() => ui.toast("Couldn't save the preference on the server.", "warn")); };
  app.savePrefs = savePrefs;
  lib.on("toggle-sound", () => { const on = !(app.prefs && app.prefs.sound); savePrefs({ sound: on, ui_sounds: on }); if (on) setTimeout(() => lib.sound.play("done"), 30); ui.toast(`Sound ${on ? "on" : "off"}`, "info"); });
  lib.on("theme-menu", (el) => { const t = (app.prefs && app.prefs.theme) || "dark"; ui.openMenu(el, [{ header: "Theme" }, ...[["dark", "Dark", "moon"], ["light", "Light", "sun"], ["system", "System", "monitor"]].map(([v, l, ic]) => ({ label: l, icon: ic, active: t === v, onSelect: () => { app.applyTheme(v); savePrefs({ theme: v }); } }))], { alignRight: true, width: 180 }); });
  lib.on("account-menu", (el) => { const me = F.me() || {}; ui.openMenu(el, [{ header: me.email || "" }, { label: "Settings", icon: "settings", to: "/settings" }, { label: "Keyboard shortcuts", icon: "keyboard", onSelect: () => ui.open("shortcuts") }, { sep: true }, { label: "Sign out", icon: "logout", danger: true, onSelect: () => lib.dispatch("sign-out", el) }], { alignRight: true, width: 240 }); });
  lib.on("help-open", () => ui.open("shortcuts"));
  lib.on("side-collapse", () => { lib.storage.set("sidebar-collapsed", !collapsed()); lib.store.emit(); });
  lib.on("ws-menu", (el) => {
    const cur = F.workspace() || {};
    const items = (app.workspaces || []).map((w) => ({ label: w.name, icon: w.personal ? "user" : "grid", active: w.id === cur.id, hint: w.role.replace("_", " ").toLowerCase(),
      onSelect: () => { if (w.id === cur.id) return; savePrefs({ workspace_id: w.id }); app.prefs.workspace_id = w.id; lib.query.clear(); lib.invalidate(); F.go("/inspection"); ui.toast(`Switched to ${w.name}`, "info"); } }));
    ui.openMenu(el, [{ header: "Workspaces" }, ...items, { sep: true }, { label: "New workspace…", icon: "plus", onSelect: () => ui.open("ws-new") }, { label: "Members & roles", icon: "users", to: "/settings/workspace" }],
      { width: 290, html: `<p class="xs muted" style="padding:8px 10px 4px;margin:0">Inspections and members are isolated per workspace. Your role: <strong>${esc((cur.role || "").replace("_", " ").toLowerCase())}</strong>.</p>` });
  });
  ui.dialogs["ws-new"] = () => ({ title: "New workspace", body: `<form class="col gap-3" data-form="ws-new" onsubmit="return false">${ui.field({ id: "ws-name", label: "Name", control: '<input class="input" id="ws-name" name="name" required minlength="2" maxlength="120" placeholder="e.g. Line 3 inspection team" autofocus>' })}<p class="xs muted" style="margin:0">You'll be its owner. Invite people from Settings → Workspace.</p></form>`,
    footer: `${ui.btn({ label: "Cancel", act: "dialog-close" })}${ui.btn({ label: "Create", variant: "primary", act: "ws-create" })}` });
  lib.on("ws-create", async (el) => {
    const name = (document.getElementById("ws-name") || {}).value || "";
    await ui.run(el, async () => {
      const w = await S.workspaces.create(name.trim());
      const me = await S.auth.me(); app.workspaces = me.workspaces || [];
      savePrefs({ workspace_id: w.id }); app.prefs.workspace_id = w.id;
      ui.close(); lib.query.clear(); lib.invalidate(); F.go("/inspection"); ui.toast(`Workspace “${w.name}” created`, "ok");
    });
  });
  lib.on("noop", () => {});
  ui.dialogs.shortcuts = () => ({ title: "Keyboard shortcuts", body: `<div class="col gap-2">${[["Ctrl/⌘ K", "Search inspections and pages"], ["?", "This help"], ["G then C / N / R / H / E / A / M", "Center, New, Review, History, Reports, Analytics, Defect AI"], ["Ctrl/⌘ V on New Inspection", "Inspect a pasted image"], ["J / K", "Next / previous detection"], ["C / R / F", "Confirm, Reject, Further review"], ["+ / − / 0", "Zoom in, out, fit"], ["M", "Mark a missed defect"], ["Esc", "Close dialogs and menus"]].map(([k, d]) => `<div class="row between small"><span class="subtle">${d}</span>${ui.kbd(k)}</div>`).join("")}</div>`, footer: ui.btn({ label: "Close", act: "dialog-close" }) });
})(V.app, V.features, V.ui, V.lib, V.services);

