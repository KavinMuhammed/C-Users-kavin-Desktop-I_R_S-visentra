/* ==========================================================================
   lib/fx3d.js — premium 3D motion layer (no libraries)
   - lib.fxRunner: the shared animation runner (used by lib/aircraft3d.js)
   - lib.ambient(canvas): depth particle field + perspective horizon grid
   - pointer-driven 3D tilt + glare on cards, spotlight on the content area
   Everything pauses off-screen / in hidden tabs and honours reduced motion.
   ========================================================================== */
(function (lib) {
  const reduce = () => (lib.motion ? lib.motion.reduced() : matchMedia("(prefers-reduced-motion: reduce)").matches);
  const light = () => { const t = document.documentElement.getAttribute("data-theme"); return t === "light" || (!t && matchMedia("(prefers-color-scheme: light)").matches); };
  const css = (n, d) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || d;
  const hex = (c) => { const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(c); return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [94, 230, 210]; };
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t].map(Math.round);
  const VIOLET = [181, 140, 255];

  /* shared pointer, normalised to -1..1 across the viewport, smoothed per frame */
  const ptr = { x: 0, y: 0, sx: 0, sy: 0 };
  addEventListener("pointermove", (e) => { ptr.x = (e.clientX / innerWidth) * 2 - 1; ptr.y = (e.clientY / innerHeight) * 2 - 1; }, { passive: true });

  function rot([x, y, z], ax, ay, az = 0) {
    let c = Math.cos(ay), s = Math.sin(ay); [x, z] = [x * c + z * s, -x * s + z * c];
    c = Math.cos(ax); s = Math.sin(ax); [y, z] = [y * c - z * s, y * s + z * c];
    if (az) { c = Math.cos(az); s = Math.sin(az); [x, y] = [x * c - y * s, x * s + y * c]; }
    return [x, y, z];
  }

  /** Runs `draw(t, dt)` on rAF while the canvas is connected, visible and the tab is shown. */
  /* Decorative animation yields while the app does CPU-heavy work on this machine (Phase 9: a local
     experimental analysis). Measured on a 2-core machine: the ambient canvas otherwise costs ~1 core. */
  const kicks = new Set(), paused = () => document.documentElement.hasAttribute("data-fx-paused");
  /* Phase 12 (P-03): decorative canvases stop after a short time without user input and keep their last frame.
     Measured on the dashboard: 1,061 ms of main-thread work per idle 5 s before this change. Any input resumes them. */
  const IDLE_MS = 8000;
  let lastInput = performance.now();
  const wake = () => { const was = performance.now() - lastInput > IDLE_MS; lastInput = performance.now(); if (was) kicks.forEach((k) => k()); };
  ["pointermove", "pointerdown", "keydown", "wheel", "touchstart", "scroll"].forEach((t) => window.addEventListener(t, wake, { passive: true, capture: true }));
  const idle = () => performance.now() - lastInput > IDLE_MS;
  lib.fxIdle = idle;
  // Decorative CSS loops (scan beam, pulsing dots, logo shimmer) pause with the canvases; spinners and progress bars do not.
  setInterval(() => { const on = idle(); if (on !== document.documentElement.hasAttribute("data-idle")) document.documentElement.toggleAttribute("data-idle", on); }, 1000);
  ["pointermove", "pointerdown", "keydown", "wheel", "touchstart"].forEach((t) => window.addEventListener(t, () => document.documentElement.removeAttribute("data-idle"), { passive: true, capture: true }));
  lib.fxPause = (on) => {
    if (!!on === paused()) return;
    document.documentElement.toggleAttribute("data-fx-paused", !!on);
    if (!on) kicks.forEach((k) => k());
  };
  function runner(canvas, draw, fps = 60, opts = {}) {
    const idleOk = () => !opts.persistent && idle();
    let raf = 0, visible = true, last = 0, t = 0;
    const io = new IntersectionObserver((es) => { visible = es[0].isIntersecting; kick(); });
    io.observe(canvas);
    const onVis = () => kick();
    document.addEventListener("visibilitychange", onVis);
    function kick() { if (!raf && visible && !document.hidden && !paused() && !idleOk()) raf = requestAnimationFrame(loop); }
    kicks.add(kick);
    function size() {
      const dpr = Math.min(1.75, devicePixelRatio || 1), W = canvas.clientWidth, H = canvas.clientHeight;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
      const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); return [g, W, H];
    }
    function loop(now) {
      raf = 0;
      if (!canvas.isConnected) { io.disconnect(); document.removeEventListener("visibilitychange", onVis); kicks.delete(kick); canvas._fx = false; return; }
      if (now - last >= 1000 / fps - 2) {
        const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016; last = now;
        if (!reduce()) t += dt;
        ptr.sx += (ptr.x - ptr.sx) * 0.06; ptr.sy += (ptr.y - ptr.sy) * 0.06;
        const [g, W, H] = size(); if (W && H) draw(g, W, H, t);
      }
      if (!reduce() && visible && !document.hidden && !paused() && !idleOk()) raf = requestAnimationFrame(loop);
    }
    raf = requestAnimationFrame(loop);
  }

  /* ---------------- Ambient depth field ---------------- */
  lib.ambient = (canvas) => {
    if (!canvas || canvas._fx) return; canvas._fx = true;
    const N = lib.motion && lib.motion.level() === "high" ? 140 : 70, stars = Array.from({ length: N }, () => [Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random()]);
    runner(canvas, (g, W, H, t) => {
      const A = [94, 234, 212], B = [90, 169, 255], GD = [69, 217, 255], lt = light(), k = (lt ? 0.45 : 1) * ({ high: 1, medium: 0.6, low: 0.3 }[canvas.dataset.intensity] || 0.6);
      g.clearRect(0, 0, W, H);
      const cx = W / 2 - ptr.sx * 24, cy = H * 0.42 - ptr.sy * 16;
      // particles drifting toward the viewer
      for (const s of stars) {
        s[2] -= 0.0009; if (s[2] <= 0.02) { s[0] = Math.random() * 2 - 1; s[1] = Math.random() * 2 - 1; s[2] = 1; }
        const p = 0.18 / s[2], x = cx + s[0] * W * p, y = cy + s[1] * H * p;
        if (x < -10 || x > W + 10 || y < -10 || y > H + 10) continue;
        const a = Math.min(1, (1 - s[2]) * 1.4) * 0.5 * k;
        g.fillStyle = rgba(s[2] < 0.15 ? GD : s[0] > 0 ? A : B, a); g.beginPath(); g.arc(x, y, Math.min(2.2, 0.35 + p * 0.6), 0, 7); g.fill();
      }
      // perspective horizon grid
      const hy = H * 0.68, depth = H - hy, scroll = (t * 0.12) % 1;
      g.lineWidth = 1;
      for (let i = 0; i < 14; i++) {
        const z = (i + 1 - scroll) / 14, y = hy + depth * z * z;
        g.strokeStyle = rgba(GD, 0.08 * z * k); g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
      }
      for (let i = -14; i <= 14; i++) {
        const x0 = cx + i * 26, x1 = cx + i * (W / 6.5);
        const gr = g.createLinearGradient(0, hy, 0, H); gr.addColorStop(0, rgba(B, 0)); gr.addColorStop(1, rgba(B, 0.12 * k));
        g.strokeStyle = gr; g.beginPath(); g.moveTo(x0, hy); g.lineTo(x1, H); g.stroke();
      }
      const hz = g.createLinearGradient(0, hy - 60, 0, hy + 30); hz.addColorStop(0, rgba(A, 0)); hz.addColorStop(0.7, rgba(GD, 0.06 * k)); hz.addColorStop(1, rgba(A, 0));
      g.fillStyle = hz; g.fillRect(0, hy - 60, W, 90);
    }, lib.motion && lib.motion.level() === "high" ? 40 : 24);
  };

  /* ---------------- Tilt, glare and spotlight ---------------- */
  const TILT = ".tilt, .card.click, .metric, .step3d";
  const setFx = (el, vars) => { el._fx = Object.assign(el._fx || {}, vars); for (const k in vars) el.style.setProperty(k, vars[k]); };
  let pending = null, queued = false;
  document.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    pending = e; if (queued) return; queued = true;
    requestAnimationFrame(() => {
      queued = false; const ev = pending; pending = null; if (!ev) return;
      const c = ev.target.closest && ev.target.closest(".content");
      if (c) { const r = c.getBoundingClientRect(); setFx(c, { "--sx": `${ev.clientX - r.left}px`, "--sy": `${ev.clientY - r.top + c.scrollTop}px` }); }
      if (reduce()) return;
      const el = ev.target.closest && ev.target.closest(TILT);
      document.querySelectorAll(".is-tilting").forEach((o) => { if (o !== el) { o.classList.remove("is-tilting"); setFx(o, { "--rx": "0deg", "--ry": "0deg" }); } });
      if (!el) return;
      const r = el.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
      const amp = r.width > 520 ? 3 : 7;
      el.classList.add("is-tilting");
      setFx(el, { "--rx": `${((0.5 - y) * amp).toFixed(2)}deg`, "--ry": `${((x - 0.5) * amp).toFixed(2)}deg`, "--gx": `${(x * 100).toFixed(1)}%`, "--gy": `${(y * 100).toFixed(1)}%` });
    });
  }, { passive: true });
  document.addEventListener("pointerleave", () => document.querySelectorAll(".is-tilting").forEach((o) => { o.classList.remove("is-tilting"); setFx(o, { "--rx": "0deg", "--ry": "0deg" }); }));

  lib.fxRunner = runner;
  lib.fxPointer = ptr;

  /** Start any 3D canvases present in the DOM (safe to call after every render). */
  lib.fx3dMount = (root = document) => {
    root.querySelectorAll('canvas[data-fx="aircraft3d"]').forEach((c) => lib.aircraft3d && lib.aircraft3d(c));
    const low = lib.motion && lib.motion.level() === "low";
    document.documentElement.toggleAttribute("data-perf-low", !!low);
    if (!low) root.querySelectorAll('canvas[data-fx="ambient"]').forEach((c) => lib.ambient(c));
  };
})(V.lib);
