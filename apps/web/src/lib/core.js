/* ==========================================================================
   lib/core.js — namespace, formatting, UI store, server-query cache, router, actions
   No application data is created here: every record comes from the API.
   ========================================================================== */
const V = (window.V = { lib: {}, services: {}, ui: {}, features: {}, app: {} });

(function (lib) {
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  lib.esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c]));
  lib.clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  lib.debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  lib.sum = (arr, fn = (x) => x) => arr.reduce((a, x) => a + fn(x), 0);
  lib.delay = (ms) => new Promise((r) => setTimeout(r, ms));
  let idc = 0;
  lib.uid = (p = "id") => `${p}_${Date.now().toString(36)}${(++idc).toString(36)}`;

  /* ---------- formatting (null → honest "—") ---------- */
  const nf = new Intl.NumberFormat("en-US");
  const NA = '<span class="na">—</span>';
  lib.NA = NA;
  lib.fmt = {
    n: (v) => (v == null || isNaN(v) ? "—" : nf.format(Math.round(v))),
    pct: (v, d = 1) => (v == null || isNaN(v) ? "—" : (v * 100).toFixed(d) + "%"),
    dec: (v, d = 3) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(d)),
    ms: (v) => (v == null ? "—" : v < 1 ? v.toFixed(2) + " ms" : v < 1000 ? v.toFixed(v < 10 ? 1 : 0) + " ms" : (v / 1000).toFixed(2) + " s"),
    dur: (s) => (s == null ? "—" : s < 60 ? s.toFixed(1) + " s" : s < 3600 ? Math.floor(s / 60) + "m " + Math.round(s % 60) + "s" : Math.floor(s / 3600) + "h " + Math.round((s % 3600) / 60) + "m"),
    bytes: (b) => (b == null ? "—" : b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : b < 1073741824 ? (b / 1048576).toFixed(1) + " MB" : (b / 1073741824).toFixed(2) + " GB"),
    date: (t) => (t ? new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—"),
    dateTime: (t) => (t ? new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }) : "—"),
    iso: (t) => (t ? new Date(t).toISOString() : ""),
    rel: (t) => {
      if (!t) return "—";
      const s = (Date.now() - new Date(t).getTime()) / 1000;
      if (s < 10) return "just now";
      if (s < 60) return Math.round(s) + "s ago";
      if (s < 3600) return Math.round(s / 60) + "m ago";
      if (s < 86400) return Math.round(s / 3600) + "h ago";
      if (s < 86400 * 14) return Math.round(s / 86400) + "d ago";
      return lib.fmt.date(t);
    },
    initials: (name = "") => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?",
    compact: (v) => (v == null ? "—" : nf.format(Math.round(v))),
    short: (id) => (id ? String(id).slice(0, 8) : "—"),
  };

  lib.storage = {
    get(k, d) { try { const v = localStorage.getItem("visentra:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("visentra:" + k, JSON.stringify(v)); } catch {} },
  };

  /* ---------- UI store: view state only (never business data) ---------- */
  const listeners = new Set();
  let scheduled = false;
  lib.store = {
    version: 0,
    emit() { if (scheduled) return; scheduled = true; requestAnimationFrame(() => { scheduled = false; listeners.forEach((l) => { try { l(); } catch (e) { console.error(e); } }); }); },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
  lib.ui = { state: {}, set(patch) { Object.assign(this.state, patch); lib.store.emit(); } };
  lib.viewState = (key, defaults) => { const k = "vs:" + key; if (!lib.ui.state[k]) lib.ui.state[k] = { ...defaults }; return lib.ui.state[k]; };

  /* ---------- server-query cache (stale-while-revalidate) ---------- */
  const cache = new Map();
  lib.query = (key, fn) => {
    let e = cache.get(key);
    const v = lib.store.version;
    if (!e) { e = { status: "loading", data: undefined, error: null, ver: -1, fetching: false, at: 0 }; cache.set(key, e); }
    if (e.ver !== v && !e.fetching) {
      e.fetching = true; const startVer = v;
      Promise.resolve().then(fn).then(
        (data) => { Object.assign(e, { data, status: "success", error: null, ver: startVer, fetching: false, at: Date.now() }); lib.store.emit(); },
        (err) => { Object.assign(e, { error: err, status: e.data !== undefined ? "success" : "error", ver: startVer, fetching: false }); if (err && err.code === "UNAUTHENTICATED") V.app.onUnauthenticated && V.app.onUnauthenticated(); lib.store.emit(); }
      );
    }
    return e;
  };
  /** Mark all server data stale; visible queries refetch in the background. */
  lib.invalidate = () => { lib.store.version++; lib.store.emit(); };
  lib.query.retry = (key) => { const e = cache.get(key); if (e) { e.ver = -1; e.status = e.data !== undefined ? "success" : "loading"; e.error = null; } lib.invalidate(); };
  lib.query.clear = () => cache.clear();

  /* ---------- router ---------- */
  const routes = [];
  lib.router = {
    current: { path: "/", params: {}, route: null, query: {} },
    beforeLeave: null,
    add(pattern, opts) { const keys = []; const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, k) => (keys.push(k), "([^/]+)")) + "/?$"); routes.push({ pattern, re, keys, ...opts }); },
    match(path) { for (const r of routes) { const m = path.match(r.re); if (m) { const params = {}; r.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1]))); return { route: r, params }; } } return null; },
    go(path, { replace = false, force = false } = {}) {
      if (!path.startsWith("/")) path = "/" + path;
      if (!force && this.beforeLeave && path !== this.current.path && this.beforeLeave(path) === false) return;
      try { if (replace) history.replaceState(null, "", "#" + path); else if (location.hash !== "#" + path) history.pushState(null, "", "#" + path); } catch {}
      this.resolve(path);
    },
    resolve(path) {
      path = path || (location.hash || "").replace(/^#/, "") || "/";
      const [p, qs] = path.split("?");
      const m = this.match(p);
      this.current = { path: p, params: m ? m.params : {}, route: m ? m.route : null, query: Object.fromEntries(new URLSearchParams(qs || "")) };
      lib.ui.state.navOpen = false;
      V.app.onRoute && V.app.onRoute();
      lib.store.emit();
    },
    start() { window.addEventListener("popstate", () => this.resolve()); window.addEventListener("hashchange", () => this.resolve()); this.resolve(); },
  };

  /* ---------- delegated actions ---------- */
  const actions = {};
  lib.on = (name, fn) => { actions[name] = fn; };
  lib.dispatch = (name, el, ev) => {
    const fn = actions[name];
    if (!fn) { console.warn("No action", name); return; }
    try { const r = fn(el, ev); if (r && r.catch) r.catch((e) => V.ui.handleError(e)); } catch (e) { V.ui.handleError(e); }
  };
  lib.bindDelegation = (root) => {
    root.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-act]");
      if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
      if (el.tagName === "FORM" || el.dataset.on === "change" || el.dataset.on === "input") return;
      if (el.tagName === "A") ev.preventDefault();
      lib.dispatch(el.dataset.act, el, ev);
    });
    root.addEventListener("change", (ev) => { const el = ev.target.closest('[data-on="change"]'); if (el) lib.dispatch(el.dataset.act, el, ev); });
    root.addEventListener("input", (ev) => { const el = ev.target.closest('[data-on="input"]'); if (el) lib.dispatch(el.dataset.act, el, ev); });
    root.addEventListener("submit", (ev) => { const f = ev.target.closest("form[data-act]"); if (f) { ev.preventDefault(); lib.dispatch(f.dataset.act, f, ev); } });
    root.addEventListener("keydown", (ev) => {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches("[data-act][role='button'], tr[data-act], [data-act].card")) { ev.preventDefault(); lib.dispatch(ev.target.dataset.act, ev.target, ev); }
    });
  };
  lib.formData = (form) => { const o = {}; new FormData(form).forEach((v, k) => { if (k in o) o[k] = [].concat(o[k], v); else o[k] = v; }); return o; };
})(V.lib);
