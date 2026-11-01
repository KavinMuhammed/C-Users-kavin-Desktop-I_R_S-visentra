/* ==========================================================================
   app/main.js — boot, session, render loop, shortcuts
   ========================================================================== */
(function (app, F, ui, lib, S) {
  const U = lib.ui;
  let lastPath = null;

  app.user = null;
  app.prefs = { sound: false, theme: "dark" };

  app.onRoute = () => { const p = lib.router.current.path; if (p !== lastPath) { lastPath = p; U.state.popover = null; requestAnimationFrame(() => { const c = document.getElementById("content"); if (c) c.scrollTop = 0; }); } };
  app.onUnauthenticated = () => { if (app.user && !U.state.dialog) ui.open("session-expired", { locked: true }); };

  app.signedIn = async (r) => {
    S.setCsrf(r.csrf_token);
    const me = await S.auth.me();
    app.user = me.user; app.prefs = { sound: false, theme: "dark", ...(me.preferences || {}) }; app.workspaces = me.workspaces || [];
    S.setCsrf(me.csrf_token);
    app.applyTheme(app.prefs.theme); app.applyPrefs();
    lib.query.clear(); lib.invalidate();
  };
  app.signedOut = () => { app.user = null; lib.query.clear(); lib.ui.state = {}; lib.router.go("/login", { force: true }); };

  const AUTH = { "/login": () => F.auth.login(), "/register": () => F.auth.register(), "/forgot": () => F.auth.forgot() };
  function viewFor(route, params) {
    if (!route) return ui.empty({ icon: "search", title: "Page not found", text: "Check the address or press Ctrl+K to search.", action: ui.btn({ label: "Inspection Center", icon: "scan", variant: "primary", act: "nav", data: { to: "/inspection" } }) });
    try { return route.view(params); } catch (e) { console.error(e); return `<div class="card">${ui.errorState({ message: "This page hit an unexpected error while rendering." })}</div>`; }
  }
  function render() {
    const root = document.getElementById("app");
    const { path, params, route } = lib.router.current;
    let html, title = "VisionTra";
    try {
      if (path === "/welcome" || (!app.user && path === "/")) {
        html = F.welcome.page(); title = "VisionTra — Aircraft surface inspection";
        if (path !== "/welcome") history.replaceState(null, "", "#/welcome");
      } else if (!app.user) {
        if (AUTH[path]) html = AUTH[path]();
        else if (path.startsWith("/reset/")) html = F.auth.reset(path.slice(7));
        else { if (path !== "/" && path !== "/login") U.state.afterLogin = path; html = F.auth.login(); if (path !== "/login") history.replaceState(null, "", "#/login"); }
        title = "Sign in · VisionTra";
      } else if (AUTH[path] || path.startsWith("/reset/") || path === "/") { lib.router.go("/inspection", { replace: true }); return; }   // the dashboard is home
      else { html = app.renderShell(route, params, viewFor(route, params)); title = `${route ? route.title : "Not found"} · VisionTra`; }
    } catch (e) { console.error(e); html = `<div class="page"><div class="card">${ui.errorState({ message: "The interface hit an unexpected error." })}</div></div>`; }
    if (document.title !== title) document.title = title;
    document.body.classList.toggle("is-welcome", path === "/welcome" || (!app.user && path === "/"));
    try { lib.morph(root, html); } catch (e) { console.error(e); root.innerHTML = html; }
    try { lib.morph(document.getElementById("overlays"), ui.renderOverlays()); } catch (e) { console.error(e); }
    try {                                                    // Phase 15 intro: covers the page on the first visit; the app stays rendered underneath
      const ir = document.getElementById("intro-root"), on = !!(F.intro && F.intro.active());
      if (ir) lib.morph(ir, on ? F.intro.safeView() : "");
      root.inert = on; document.body.classList.toggle("vti-open", on);
      if (on && ir && !ir.dataset.focused) { ir.dataset.focused = "1"; setTimeout(() => { const b = ir.querySelector("[data-act=intro-start]") || ir.querySelector("[data-act=intro-skip]"); b && b.focus(); }, 50); }
      if (!on && ir) delete ir.dataset.focused;
    } catch (e) { console.error(e); }
    try { if (path === "/welcome") {} else if (!app.user) F.auth.mount(); else if (route && route.mount) route.mount(params); } catch (e) { console.error(e); }
    try { lib.fx3dMount(); } catch (e) { console.error(e); }
    try { lib.ac3dMount(); } catch (e) { console.error(e); }
  }
  app.render = render;

  /* The page and the server process must come from the same build. Python loads the server code once, at start-up, so
     after an update an old process keeps answering with old endpoints while the browser receives the new page. Detect
     that and say so plainly instead of leaving parts of the page waiting. */
  async function checkServerVersion() {
    try {
      const v = await S.request("GET", "/api/system/version");
      const page = window.VT_BUILD || null;
      const restartedSinceLoad = v.started_at && Date.parse(v.started_at) > PAGE_LOADED_AT;
      app.serverStale = page && v.frontend_build !== page ? { server: v.frontend_build || "older version", page, started: v.started_at || null, pageOld: !!(restartedSinceLoad && v.frontend_build) } : null;
    } catch (e) { app.serverStale = null; }
    lib.store.emit();
  }
  app.checkServerVersion = checkServerVersion;
  // Phase 19: a tab left open across a server update kept showing the old page. Re-check every minute and on focus.
  const PAGE_LOADED_AT = Date.now();
  setInterval(() => { if (document.visibilityState === "visible" && app.user) checkServerVersion(); }, 60000);
  window.addEventListener("focus", () => { if (app.user) checkServerVersion(); });

  async function boot() {
    lib.bindDelegation(document.body);
    lib.bindTooltips();
    lib.store.subscribe(render);
    app.applyTheme(lib.storage.get("theme-boot", "dark"));
    try { const me = await S.auth.me(); app.user = me.user; app.workspaces = me.workspaces || []; app.prefs = { sound: false, theme: "dark", ...(me.preferences || {}) }; S.setCsrf(me.csrf_token); app.applyTheme(app.prefs.theme); app.applyPrefs(); }
    catch (e) { if (e.code === "NETWORK") document.getElementById("app").innerHTML = `<div class="page"><div class="card">${ui.errorState(e)}</div></div>`; }
    lib.router.start();
    let g = false, gt;
    document.addEventListener("keydown", (e) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { if (app.user) { e.preventDefault(); lib.dispatch(U.state.cmdk ? "cmdk-close" : "cmdk-open"); } return; }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || U.state.dialog || U.state.cmdk || !app.user) return;
      if (e.key === "?") { ui.open("shortcuts"); return; }
      if (e.key === "g") { g = true; clearTimeout(gt); gt = setTimeout(() => (g = false), 900); return; }
      if (g) { const m = { c: "/inspection", n: "/inspection/new", r: "/inspection/review", h: "/inspection/history", e: "/inspection/reports", a: "/inspection/analytics", m: "/inspection/model", s: "/system" }[e.key]; g = false; if (m) { e.preventDefault(); lib.router.go(m); } }
    });
    window.addEventListener("unhandledrejection", (e) => { const r = e.reason || {}; if (r.code) ui.handleError(r); else console.error(r); });
    render();
    checkServerVersion();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})(V.app, V.features, V.ui, V.lib, V.services);
