/* ==========================================================================
   features/_shared.js — session, workspace context, shared actions
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc;
  F.go = (to) => lib.router.go(to);
  F.q = (key, fn) => lib.query(key, fn);
  F.me = () => V.app.user;

  /** Active workspace: the one saved in preferences if the user still belongs to it, else the first (personal). */
  F.workspace = () => { const ws = V.app.workspaces || []; const id = V.app.prefs && V.app.prefs.workspace_id; return ws.find((w) => w.id === id) || ws[0] || null; };

  F.copy = async (text) => {
    try { await navigator.clipboard.writeText(text); ui.toast("Copied to clipboard", "ok"); }
    catch { ui.toast("Copy isn't available here; select the text and copy it manually.", "warn"); }
  };
  /** Run a mutation: busy state, invalidate server data, toast, sound. */
  F.mutate = async (el, fn, success, sound) => {
    const r = await ui.run(el, fn, success);
    if (r !== undefined) { lib.invalidate(); if (sound) lib.sound.play(sound); }
    return r;
  };

  lib.on("nav", (el) => { if (el.dataset.to) F.go(el.dataset.to); });
  lib.on("query-retry", (el) => (el.dataset.key ? lib.query.retry(el.dataset.key) : lib.invalidate()));
  lib.on("copy-text", (el) => F.copy(el.dataset.text));
  F.esc = esc;
})(V.features, V.ui, V.lib, V.services);
