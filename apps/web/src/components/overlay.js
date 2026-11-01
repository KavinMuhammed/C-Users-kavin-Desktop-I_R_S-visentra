/* ==========================================================================
   components/overlay.js — Dialog, Drawer, Popover menu, Toast, Confirm,
   focus management and global error presentation
   ========================================================================== */
(function (ui, lib) {
  const esc = lib.esc, I = lib.icon, U = lib.ui;
  ui.dialogs = {};      // type -> (props) => {title, sub, body, footer, wide, drawer}
  const callbacks = new Map();
  let lastFocus = null;

  /* ---------- toasts ---------- */
  ui.toast = (text, tone = "ok", opts = {}) => {
    const id = lib.uid("t");
    const list = (U.state.toasts || []).concat({ id, text, tone, action: opts.action, actionLabel: opts.actionLabel });
    U.set({ toasts: list.slice(-4) });
    setTimeout(() => U.set({ toasts: (U.state.toasts || []).filter((t) => t.id !== id) }), opts.ms || (tone === "bad" ? 7000 : 4200));
  };
  lib.on("toast-close", (el) => U.set({ toasts: (U.state.toasts || []).filter((t) => t.id !== el.dataset.id) }));
  lib.on("toast-action", (el) => { const t = (U.state.toasts || []).find((x) => x.id === el.dataset.id); if (t && t.action) lib.dispatch(t.action.act, { dataset: t.action.data || {} }); U.set({ toasts: (U.state.toasts || []).filter((x) => x.id !== el.dataset.id) }); });

  /* ---------- dialogs & drawers ---------- */
  ui.open = (type, props = {}) => { lastFocus = document.activeElement; U.set({ dialog: { type, props, key: lib.uid("dlg") }, popover: null }); requestAnimationFrame(() => requestAnimationFrame(focusDialog)); };
  ui.close = () => { U.set({ dialog: null }); if (lastFocus && lastFocus.isConnected) setTimeout(() => lastFocus.focus(), 30); };
  ui.setDialogProps = (patch) => { const d = U.state.dialog; if (d) U.set({ dialog: { ...d, props: { ...d.props, ...patch } } }); };
  function focusDialog() { const d = document.querySelector("[data-dialog]"); if (!d) return; const f = d.querySelector("[autofocus], input:not([type=hidden]):not([disabled]), select, textarea, button.primary, button"); (f || d).focus(); }
  lib.on("dialog-close", () => ui.close());
  lib.on("overlay-click", (el, ev) => { if (ev.target === el && !((U.state.dialog || {}).props || {}).locked) ui.close(); });

  ui.confirm = ({ title, body, confirmLabel = "Confirm", tone = "danger", requireText, onConfirm }) => {
    const id = lib.uid("cb"); callbacks.set(id, onConfirm);
    ui.open("confirm", { title, body, confirmLabel, tone, requireText, cb: id });
  };
  ui.dialogs.confirm = (p) => ({
    title: p.title, role: "alertdialog",
    body: `<form id="dlg-form" data-act="confirm-submit" data-cb="${p.cb}" class="col gap-4"><div class="subtle">${p.body}</div>${p.requireText ? `<div class="field"><label for="confirm-text">Type <strong class="mono">${esc(p.requireText)}</strong> to confirm</label><input class="input" id="confirm-text" autocomplete="off" data-act="confirm-type" data-on="input"></div>` : ""}${p.error ? ui.banner("bad", "alert", esc(p.error)) : ""}</form>`,
    footer: `${ui.btn({ label: "Cancel", act: "dialog-close" })}${ui.btn({ label: p.confirmLabel, variant: p.tone === "danger" ? "danger" : "primary", type: "submit", cls: "", busy: p.busy, disabled: p.requireText && p.typed !== p.requireText, data: {}, id: "confirm-go" }).replace("<button", '<button form="dlg-form"')}`,
  });
  lib.on("confirm-type", (el) => ui.setDialogProps({ typed: el.value }));
  lib.on("confirm-submit", async (form) => {
    const cb = callbacks.get(form.dataset.cb); if (!cb) return ui.close();
    ui.setDialogProps({ busy: true, error: null });
    try { await cb(); callbacks.delete(form.dataset.cb); ui.close(); }
    catch (e) { if (e.code === "UNAUTHENTICATED") { ui.close(); return ui.handleError(e); } ui.setDialogProps({ busy: false, error: e.message }); }
  });

  /* ---------- popover menus ---------- */
  ui.openMenu = (anchor, items, opts = {}) => {
    const r = anchor.getBoundingClientRect();
    const id = lib.uid("mn"); items.forEach((it, i) => { if (it.onSelect) callbacks.set(id + ":" + i, it.onSelect); });
    U.set({ popover: { id, items: items.map(({ onSelect, ...x }) => x), x: r.left, y: r.bottom + 6, right: innerWidth - r.right, alignRight: opts.alignRight, width: opts.width, html: opts.html } });
    requestAnimationFrame(() => { const m = document.querySelector(".popover .menu-item"); m && m.focus(); });
  };
  lib.on("menu-select", (el) => { const fn = callbacks.get(el.dataset.cb); U.set({ popover: null }); if (fn) fn(); else if (el.dataset.to) lib.router.go(el.dataset.to); });
  ui.renderPopover = () => {
    const p = U.state.popover; if (!p) return "";
    const pos = p.alignRight ? `right:${Math.max(8, p.right)}px` : `left:${Math.max(8, Math.min(p.x, innerWidth - (p.width || 240) - 8))}px`;
    return `<div class="popover" role="menu" style="position:fixed;top:${p.y}px;${pos};${p.width ? `width:${p.width}px` : ""};max-width:calc(100vw - 16px)" data-key="${p.id}">${p.html || ""}${p.items.map((it, i) => it.sep ? '<div class="menu-sep" role="separator"></div>' : it.header ? `<div class="cmdk-group">${esc(it.header)}</div>` : `<button class="menu-item ${it.danger ? "danger" : ""} ${it.active ? "active" : ""}" role="menuitem" data-act="menu-select" data-cb="${p.id}:${i}" ${it.to ? `data-to="${esc(it.to)}"` : ""} ${it.disabled ? "disabled" : ""}>${it.icon ? I(it.icon) : ""}<span class="grow truncate">${esc(it.label)}</span>${it.active ? I("check") : ""}${it.hint ? `<span class="xs muted">${esc(it.hint)}</span>` : ""}</button>`).join("")}</div>`;
  };

  /* ---------- render overlay layer ---------- */
  ui.renderOverlays = () => {
    let h = "";
    const d = U.state.dialog;
    if (d && ui.dialogs[d.type]) {
      let spec;
      try { spec = ui.dialogs[d.type](d.props || {}); } catch (e) { console.error(e); spec = { title: "Something went wrong", body: ui.errorState({ message: "This dialog failed to render." }), footer: ui.btn({ label: "Close", act: "dialog-close" }) }; }
      if (spec.drawer) h += `<div class="overlay side" data-act="overlay-click" data-key="ov-${d.key}"><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="dlg-title" data-dialog tabindex="-1"><div class="dialog-h" style="border-bottom:1px solid var(--border);padding-bottom:var(--s4)"><div class="col" style="gap:2px"><h2 id="dlg-title" style="font-size:var(--fs-lg)">${spec.title}</h2>${spec.sub ? `<span class="small muted">${spec.sub}</span>` : ""}</div>${ui.iconBtn("x", "Close", "dialog-close")}</div><div class="dialog-b" style="flex:1">${spec.body}</div>${spec.footer ? `<div class="dialog-f">${spec.footer}</div>` : ""}</aside></div>`;
      else h += `<div class="overlay" data-act="overlay-click" data-key="ov-${d.key}"><div class="dialog ${spec.wide ? "wide" : ""}" role="${spec.role || "dialog"}" aria-modal="true" aria-labelledby="dlg-title" data-dialog tabindex="-1"><div class="dialog-h"><div class="col" style="gap:4px"><h2 id="dlg-title" style="font-size:var(--fs-lg)">${spec.title}</h2>${spec.sub ? `<span class="small muted">${spec.sub}</span>` : ""}</div>${(d.props || {}).locked ? "" : ui.iconBtn("x", "Close dialog", "dialog-close")}</div><div class="dialog-b">${spec.body}</div>${spec.footer ? `<div class="dialog-f">${spec.footer}</div>` : ""}</div></div>`;
    }
    if (U.state.cmdk && V.features.search) h += V.features.search.renderPalette();
    h += ui.renderPopover();
    h += `<div class="toasts" aria-live="polite" aria-atomic="false">${(U.state.toasts || []).map((t) => `<div class="toast ${t.tone}" role="${t.tone === "bad" ? "alert" : "status"}" data-key="${t.id}">${I(t.tone === "bad" ? "alert_circle" : t.tone === "warn" ? "alert" : t.tone === "info" ? "info" : "check_circle")}<span class="grow">${esc(t.text)}</span>${t.action ? `<button data-act="toast-action" data-id="${t.id}" style="opacity:1;font-weight:600;text-decoration:underline">${esc(t.actionLabel || "Undo")}</button>` : ""}<button data-act="toast-close" data-id="${t.id}" aria-label="Dismiss">${I("x")}</button></div>`).join("")}</div>`;
    return h;
  };

  /* keyboard: Esc closes, Tab is trapped inside the dialog */
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (U.state.popover) { U.set({ popover: null }); e.preventDefault(); return; }
      if (U.state.cmdk) { U.set({ cmdk: null }); e.preventDefault(); return; }
      if (U.state.dialog && !(U.state.dialog.props || {}).locked) { ui.close(); e.preventDefault(); return; }
    }
    if (e.key === "Tab" && U.state.dialog) {
      const d = document.querySelector("[data-dialog]"); if (!d) return;
      const f = [...d.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])')].filter((x) => x.offsetParent !== null);
      if (!f.length) return; const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); } else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
    }
  });
  document.addEventListener("mousedown", (e) => { if (U.state.popover && !e.target.closest(".popover") && !e.target.closest("[data-menu-anchor]")) U.set({ popover: null }); });

  /* ---------- global error presentation ---------- */
  ui.handleError = (e) => {
    if (!e) return;
    console.warn("[visionora]", e.code || "", e.message);
    if (e.code === "UNAUTHENTICATED") return V.app.onUnauthenticated && V.app.onUnauthenticated();
    if (e.code === "NETWORK") return ui.toast(e.message + " Your change was not saved.", "bad", { action: { act: "query-retry-all" }, actionLabel: "Retry" });
    if (e.code === "FORBIDDEN") return ui.toast(e.message, "warn");
    ui.toast(e.message && !/undefined|null|TypeError|of undefined/.test(e.message) ? e.message : "Something went wrong. Please try again.", "bad");
  };
  /** Run an async action with button busy state and error handling */
  ui.run = async (el, fn, success) => {
    if (el && el.classList) { el.classList.add("is-busy"); el.setAttribute("aria-busy", "true"); }
    try { const r = await fn(); if (success) ui.toast(typeof success === "function" ? success(r) : success, "ok"); return r; }
    catch (e) { ui.handleError(e); return undefined; }
    finally { if (el && el.classList) { el.classList.remove("is-busy"); el.removeAttribute("aria-busy"); } }
  };
})(V.ui, V.lib);
