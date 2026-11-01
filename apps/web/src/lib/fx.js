/* ==========================================================================
   lib/fx.js — optional sound cues (Web Audio, off by default) and the
   lightweight 3D "vision lattice" used on the sign-in screen.
   ========================================================================== */
(function (lib) {
  /* ---------- SoundManager: synthesized cues (no audio files), never autoplayed ----------
     Events map to short, quiet tones. Volume = master × channel. Respects the user's
     preference (off by default) and only creates the AudioContext after a user gesture. */
  let ctx = null;
  const N = (f, d, type = "sine") => [f, d, type];
  const EVENTS = {
    "app.intro": { ch: "ui", notes: [N(392, 0.18), N(523, 0.2), N(784, 0.42)] },
    "ui.hover": { ch: "ui", notes: [N(1400, 0.025)], gain: 0.25 },
    "ui.click": { ch: "ui", notes: [N(900, 0.04)], gain: 0.5 },
    "ui.success": { ch: "ui", notes: [N(660, 0.07), N(990, 0.11)] },
    "ui.error": { ch: "notifications", notes: [N(220, 0.12), N(175, 0.18)] },
    "ui.notification": { ch: "notifications", notes: [N(740, 0.06), N(988, 0.09)] },
    "training.start": { ch: "training", notes: [N(440, 0.07), N(660, 0.1)] },
    "training.complete": { ch: "training", notes: [N(523, 0.08), N(659, 0.08), N(784, 0.16)] },
    "device.connected": { ch: "notifications", notes: [N(587, 0.06), N(880, 0.1)] },
    "device.disconnected": { ch: "notifications", notes: [N(659, 0.07), N(440, 0.12)] },
    "review.approved": { ch: "ui", notes: [N(587, 0.07), N(784, 0.1)] },
    // Phase 19 inspection cues: a rising scan sweep, then a two-tone result (found → attention, clear → resolved)
    "scan.start": { ch: "ui", notes: [N(330, 0.05, "triangle"), N(494, 0.05, "triangle"), N(740, 0.08, "triangle")] },
    "scan.found": { ch: "notifications", notes: [N(880, 0.08), N(660, 0.08), N(880, 0.16)] },
    "scan.clear": { ch: "notifications", notes: [N(523, 0.07), N(784, 0.16)] },
  };
  // legacy cue names used across features
  const ALIAS = { upload: "ui.success", start: "training.start", done: "training.complete", predict: "ui.success", review: "review.approved", error: "ui.error" };
  const prefs = () => (V.app && V.app.prefs) || {};
  lib.sound = {
    EVENTS: Object.keys(EVENTS),
    enabled: () => !!prefs().sound,
    volume(ch) { const v = prefs().volume || {}; const m = v.master == null ? 0.7 : v.master; const c = v[ch] == null ? 0.8 : v[ch]; return m * c; },
    play(name, { force = false } = {}) {
      const ev = EVENTS[ALIAS[name] || name];
      if (!ev || (!force && !this.enabled())) return;
      const vol = this.volume(ev.ch) * (ev.gain || 1) * 0.07;
      if (vol <= 0) return;
      try {
        ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
        if (ctx.state === "suspended") ctx.resume();
        let t = ctx.currentTime + 0.01;
        for (const [f, d, type] of ev.notes) {
          const o = ctx.createOscillator(), g = ctx.createGain();
          o.type = type; o.frequency.value = f;
          g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + 0.012); g.gain.exponentialRampToValueAtTime(0.0001, t + d);
          o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + d + 0.02); t += d * 0.8;
        }
      } catch { /* audio unavailable: silently skip, UI never depends on sound */ }
    },
  };
  // subtle UI cues, only when the user enabled sound and the "ui" channel is audible
  document.addEventListener("click", (e) => { if (e.target.closest(".btn, .nav-item, [role=tab]") && prefs().ui_sounds) lib.sound.play("ui.click"); }, true);

  /* ---------- Notification center ----------
     Server notifications (GET /api/notifications) are created from real audit events on the
     server and are the source of truth. Local items are only for events that exist solely in
     this browser (e.g. a local camera stream ending). */
  const key = () => "notif:" + ((V.app && V.app.user && V.app.user.id) || "anon");
  const TONE = { success: "ok", warning: "warn", error: "bad", info: "info" };
  const ICON = { "training.completed": "activity", "training.failed": "activity", "training.cancelled": "activity", "model.promoted": "cube", "model.validated": "target",
    "review.required": "check_square", "dataset.import.completed": "download", "data_source.failed": "download", "dataset.readiness.changed": "gauge",
    "device.connected": "video", "device.offline": "video", "inference.batch.completed": "scan" };
  let server = [], serverUnread = 0, seen = null, syncing = null;
  lib.notify = {
    list() {
      const s = server.map((n) => ({ id: n.id, server: true, at: n.created_at, read: !!n.read_at, title: n.title, text: n.message, tone: TONE[n.severity] || "info", to: n.link, icon: ICON[n.type], kind: n.type }));
      return [...s, ...lib.storage.get(key(), [])].sort((a, b) => (a.at < b.at ? 1 : -1));
    },
    unread() { return serverUnread + lib.storage.get(key(), []).filter((n) => !n.read).length; },
    /** Pull the latest server notifications; new unread ones play a sound once. */
    sync() {
      if (syncing || !V.services.notifications) return syncing;
      syncing = V.services.notifications.list({ limit: 40 }).then((r) => {
        const fresh = seen ? r.items.filter((n) => !n.read_at && !seen.has(n.id)) : [];
        server = r.items; serverUnread = r.unread; seen = new Set(r.items.map((n) => n.id));
        if (fresh.length) lib.sound.play(fresh.some((n) => n.severity === "error") ? "ui.error" : (fresh[0].type === "training.completed" ? "training.complete" : "ui.notification"));
        lib.store.emit();
      }).catch(() => {}).finally(() => { syncing = null; });
      return syncing;
    },
    reset() { server = []; serverUnread = 0; seen = null; },
    push(n) {
      const list = lib.storage.get(key(), []);
      list.unshift({ id: lib.uid("n"), at: new Date().toISOString(), read: false, ...n });
      lib.storage.set(key(), list.slice(0, 40));
      lib.sound.play(n.tone === "bad" ? "ui.error" : n.sound || "ui.notification");
      lib.store.emit();
    },
    markAll() {
      lib.storage.set(key(), lib.storage.get(key(), []).map((n) => ({ ...n, read: true })));
      if (serverUnread && V.services.notifications) { server = server.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() })); serverUnread = 0; V.services.notifications.readAll().catch(() => {}); }
      lib.store.emit();
    },
    clear() { lib.storage.set(key(), []); this.markAll(); },
  };

  /* ---------- performance / motion level: high | balanced | low ---------- */
  lib.motion = {
    reduced: () => matchMedia("(prefers-reduced-motion: reduce)").matches || prefs().motion === "off",
    level() {
      const m = prefs().perf;
      if (m === "high" || m === "balanced" || m === "low") return m;
      const cores = navigator.hardwareConcurrency || 4, mem = navigator.deviceMemory || 4;
      return cores <= 4 || mem <= 4 ? "balanced" : "high";
    },
  };

  /* ---------- 3D lattice (canvas, no library). Pauses offscreen, honours reduced motion. ---------- */
  lib.lattice = (canvas) => {
    if (!canvas || canvas._lattice) return;
    canvas._lattice = true;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const g = canvas.getContext("2d");
    const layers = [6, 9, 9, 5];
    const nodes = [];
    layers.forEach((n, li) => { for (let i = 0; i < n; i++) nodes.push({ x: (li - (layers.length - 1) / 2) * 1.25, y: (i - (n - 1) / 2) * 0.42, z: Math.sin(i * 1.7 + li) * 0.5, l: li }); });
    const edges = [];
    nodes.forEach((a, i) => nodes.forEach((b, j) => { if (b.l === a.l + 1 && ((i * 7 + j * 3) % 3 !== 0)) edges.push([i, j]); }));
    let t = 0, raf = 0, visible = true;
    const io = new IntersectionObserver((es) => { visible = es[0].isIntersecting; if (visible && !raf) loop(); });
    io.observe(canvas);
    function frame() {
      const dpr = Math.min(2, devicePixelRatio || 1), W = canvas.clientWidth, H = canvas.clientHeight;
      if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      const ry = t * 0.25, rx = 0.35 + Math.sin(t * 0.2) * 0.08, s = Math.min(W, H) * 0.36;
      const proj = nodes.map((n) => { const x1 = n.x * Math.cos(ry) - n.z * Math.sin(ry), z1 = n.x * Math.sin(ry) + n.z * Math.cos(ry); const y1 = n.y * Math.cos(rx) - z1 * Math.sin(rx), z2 = n.y * Math.sin(rx) + z1 * Math.cos(rx); const p = 3.2 / (3.2 + z2); return [W / 2 + x1 * s * p, H / 2 + y1 * s * p, p]; });
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7cf0e0";
      g.lineWidth = 1;
      edges.forEach(([a, b], k) => { const pa = proj[a], pb = proj[b]; const pulse = (Math.sin(t * 2 - k * 0.37) + 1) / 2; g.strokeStyle = accent; g.globalAlpha = 0.06 + pulse * 0.22 * pa[2]; g.beginPath(); g.moveTo(pa[0], pa[1]); g.lineTo(pb[0], pb[1]); g.stroke(); });
      proj.forEach((p, i) => { g.globalAlpha = 0.5 + 0.5 * ((Math.sin(t * 1.5 + i) + 1) / 2) * p[2]; g.fillStyle = accent; g.beginPath(); g.arc(p[0], p[1], 2.2 * p[2] + 0.8, 0, 7); g.fill(); });
      g.globalAlpha = 1;
    }
    function loop() { raf = 0; if (!canvas.isConnected) { io.disconnect(); return; } frame(); if (!reduce && visible) { t += 0.012; raf = requestAnimationFrame(loop); } }
    loop();
  };
})(V.lib);
