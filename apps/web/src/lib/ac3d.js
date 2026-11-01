/* ==========================================================================
   lib/ac3d.js — mounts the 3D aircraft (modules/aircraft_scene.mjs, Three.js)
   into [data-ac3d] containers, lazily, after every render.

   lib.ac3dView({ key, mode, zones, focus, label, hud }) → HTML for a viewer with
   view presets, pause/play and a legend. The WebGL scene loads on first use;
   without WebGL (or if the module fails to load) the lightweight canvas wireframe
   (lib/aircraft3d.js) is shown instead with a note — the page never waits on it.
   ========================================================================== */
(function (lib) {
  const esc = lib.esc, I = lib.icon;
  const st = (key) => lib.viewState("ac3d:" + key, { paused: null, note: "", force: false, canForce: false, view: "three_quarter", credit: "", creditFull: "" });
  const reducedMotion = () => (lib.motion && lib.motion.reduced && lib.motion.reduced()) || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  let modP = null;
  const loadModule = () => (modP = modP || import(`/vendor/aircraft_scene.mjs?v=${encodeURIComponent(window.VT_BUILD || "dev")}`));
  const PRESETS = [["three_quarter", "3/4", "Three-quarter view (1)"], ["front", "Front", "Front view (2)"], ["side", "Side", "Side view (3)"], ["top", "Top", "Top view (4)"]];

  lib.ac3dView = ({ key, mode = "map", zones = {}, focus = "", label = "3D aircraft", hud = "", legend = true, cls = "" }) => {
    const v = st(key), paused = v.paused == null ? reducedMotion() : v.paused;
    return `<div class="ac3d-view ${cls}" data-ac3d data-key="ac3d-${esc(key)}" data-ac3d-key="${esc(key)}" data-mode="${esc(mode)}" data-label="${esc(label)}" data-zones='${esc(JSON.stringify(zones))}' data-focus="${esc(focus || "")}">
      <div class="ac3d-stage" data-static="ac3d" tabindex="0" role="img" aria-label="${esc(label)}. Drag to orbit, scroll to zoom; arrow keys orbit, plus and minus zoom, 1 to 4 choose a view, 0 resets, space pauses."></div>
      ${hud ? `<div class="ac3d-hud">${hud}</div>` : ""}
      <div class="ac3d-ctl" role="toolbar" aria-label="3D view controls">
        ${PRESETS.map(([id, l, t]) => `<button type="button" class="ac3d-btn" data-act="ac3d-preset" data-v="${id}" title="${t}" aria-pressed="${v.view === id}">${l}</button>`).join("")}
        <span class="ac3d-sep" aria-hidden="true"></span>
        <button type="button" class="ac3d-btn" data-act="ac3d-reset" title="Reset the camera (0)">${I("refresh")}<span class="sr-only">Reset camera</span></button>
        <button type="button" class="ac3d-btn" data-act="ac3d-pause" aria-pressed="${paused}" title="${paused ? "Resume rotation (space)" : "Pause rotation (space)"}">${I(paused ? "play" : "stop")}<span class="sr-only">${paused ? "Resume rotation" : "Pause rotation"}</span></button>
      </div>
      ${legend ? `<div class="ac3d-legend"><span><i class="lg-c"></i>Confirmed</span><span><i class="lg-a"></i>AI proposals</span>${focus ? `<span><i class="lg-f"></i>This inspection</span>` : ""}</div>` : ""}
      <div class="ac3d-note" data-ac3d-note role="status">${esc(v.note || "")}</div>
      <div class="ac3d-credit" data-ac3d-credit title="${esc(v.creditFull || "")}">${esc(v.credit || "")}</div>
      ${v.canForce ? `<button type="button" class="ac3d-btn ac3d-force" data-act="ac3d-force">Load full 3D model</button>` : ""}
    </div>`;
  };

  const mark = (el, c, on = true) => { el._keepClass = el._keepClass || new Set(); if (on) { el._keepClass.add(c); el.classList.add(c); } else { el._keepClass.delete(c); el.classList.remove(c); } };

  /* The probe context is created with the renderer's own attributes and handed to the first 3D view (spare), so
     the page pays for one WebGL context start-up instead of two (~0.2 s + ~0.8 s on a hybrid-GPU laptop). */
  let glInfo = null, spare = null;
  const GL_ATTRS = { alpha: true, antialias: true, depth: true, stencil: true, premultipliedAlpha: true, preserveDrawingBuffer: false, powerPreference: "high-performance" };
  function probeGL() {
    if (glInfo) return glInfo;
    try {
      const c = document.createElement("canvas"), gl2 = window.WebGL2RenderingContext && c.getContext("webgl2", GL_ATTRS);
      const gl = gl2 || (window.WebGLRenderingContext && c.getContext("webgl"));
      if (!gl) return (glInfo = { ok: false });
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      const name = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
      const soft = /swiftshader|llvmpipe|softpipe|basic render|software/i.test(name);
      if (gl2 && !soft) spare = { canvas: c, context: gl2 };
      else { const lc = gl.getExtension("WEBGL_lose_context"); lc && lc.loseContext(); }
      return (glInfo = { ok: true, soft, name });
    } catch { return (glInfo = { ok: false }); }
  }
  const takeSpare = () => { const s = spare; spare = null; return s && !s.context.isContextLost() ? s : {}; };
  lib.ac3dInfo = () => probeGL();

  function fallback(el, reason, canForce) {
    const stage = el.querySelector(".ac3d-stage");
    if (!stage || stage.querySelector(".airframe-static")) return;
    stage.innerHTML = `<svg class="airframe-static" viewBox="0 0 600 300" role="img" aria-label="Static fighter-inspired concept; 3D unavailable"><path fill="#aab1b8" stroke="#e1e0db" stroke-width="2" d="M530 150 410 127 330 113 230 34 190 34 240 126 115 123 66 83 45 85 88 142 70 146 70 154 88 158 45 215 66 217 115 177 240 174 190 266 230 266 330 187 410 173Z"/><path fill="#29251e" stroke="#d9b871" d="M450 150 Q408 126 362 150 Q408 174 450 150Z"/><path fill="#17191d" d="M82 139H125V149H82Z M82 151H125V161H82Z"/></svg>`;
    stage.setAttribute("aria-label", "Static aircraft reference. Interactive 3D is unavailable.");
    Object.assign(st(el.dataset.ac3dKey), { credit: "", creditFull: "" }); const cr = el.querySelector("[data-ac3d-credit]"); if (cr) { cr.textContent = ""; cr.title = ""; }
    mark(el, "is-fallback"); st(el.dataset.ac3dKey).note = reason;
    const n = el.querySelector("[data-ac3d-note]"); if (n) n.textContent = reason;
    st(el.dataset.ac3dKey).canForce = !!canForce;
    el.dispatchEvent(new CustomEvent("ac3d-ready", { bubbles: true }));   // the static aircraft is ready too

    queueMicrotask(() => lib.store.emit());               // show the note / opt-in button
  }

  const live = new Set();
  function sweep() { for (const el of [...live]) if (!document.body.contains(el)) { try { el._ac && el._ac.destroy(); } catch {} el._ac = null; live.delete(el); } }

  function mountOne(el) {
    // The canvas subtree is static across renders, but its evidence label is not.
    const currentStage = el.querySelector(".ac3d-stage");
    if (currentStage && !el.classList.contains("is-fallback")) currentStage.setAttribute("aria-label", `${el.dataset.label || "3D aircraft"}. Drag to orbit, scroll to zoom; arrow keys orbit, plus and minus zoom, 1 to 4 choose a view, 0 resets, space pauses.`);
    const zones = (() => { try { return JSON.parse(el.dataset.zones || "{}"); } catch { return {}; } })();
    if (el._ac) {                                      // already running: follow data changes
      if (el._zonesSig !== el.dataset.zones) { el._zonesSig = el.dataset.zones; el._ac.setZones(zones, el.dataset.focus || ""); }
      return;
    }
    if (el._acLoading) return;
    el._acLoading = true; el._zonesSig = el.dataset.zones;
    const stage = el.querySelector(".ac3d-stage");
    const gpu = probeGL(), vs = st(el.dataset.ac3dKey);
    if (!gpu.ok) { el._acLoading = false; return fallback(el, "3D rendering (WebGL) is not available here — showing a simplified aircraft."); }
    // a software rasteriser (GPU blocklisted / disabled) would stall the page for seconds while it
    // compiles shaders, so the light wireframe is shown unless the user asks for the full model
    if (gpu.soft && !vs.force && !lib.storage.get("ac3d-force", false) && !/[?&]gl=full\b/.test(location.search)) { el._acLoading = false; return fallback(el, "Hardware 3D acceleration is off in this browser — showing a simplified aircraft.", true); }
    mark(el, "is-loading");
    const v = st(el.dataset.ac3dKey);
    loadModule().then((m) => {
      el._acLoading = false;
      if (!document.body.contains(el)) return;
      const paused = v.paused == null ? reducedMotion() : v.paused;
      // soft: reuse the one cached GPU probe instead of letting every mount create a throwaway WebGL context
      el._ac = m.mount(stage, { ...(gpu.soft ? {} : takeSpare()), mode: el.dataset.mode, preset: v.view, entrance: el.dataset.ac3dKey === "intro", soft: !!gpu.soft, zones, focus: el.dataset.focus || "", reducedMotion: reducedMotion(), autorotate: !paused, onPause: (p) => { v.paused = p; lib.store.emit(); },
        onView: (name) => { const vs = st(el.dataset.ac3dKey); if (vs.view !== name) { vs.view = name; lib.store.emit(); } },
        onError: (reason) => { el._ac = null; fallback(el, reason); },
        onFirstFrame: () => { mark(el, "is-loading", false); mark(el, "is-ready"); el.dispatchEvent(new CustomEvent("ac3d-ready", { bubbles: true })); },
        // the NASA credit is shown only while the NASA model is the one on screen
        onModel: (m) => { const vs = st(el.dataset.ac3dKey); vs.credit = (m && (m.creditShort || m.credit)) || ""; vs.creditFull = (m && m.credit) || "";
          const n = el.querySelector("[data-ac3d-credit]"); if (n) { n.textContent = vs.credit; n.title = vs.creditFull; } },
        onDegrade: (msg) => { st(el.dataset.ac3dKey).note = msg; const n = el.querySelector("[data-ac3d-note]"); if (n) n.textContent = msg; mark(el, "is-lite"); } });
      live.add(el);
    }).catch((e) => {
      el._acLoading = false; mark(el, "is-loading", false);
      console.warn("3D aircraft unavailable:", e && e.message);
      fallback(el, "The 3D aircraft could not be loaded — showing a simplified aircraft.");
    });
  }

  /* Obsidian Nexus (intro freeze fix): while the intro covers the page, only its own 3D view runs. Views underneath
     are neither created (a second WebGL context, lighting bake, model fetch and shader compile used to compete with the
     intro for the main thread: measured 1.4 s + ~2 s of long tasks) nor drawn; they start or resume once it closes. */
  const introOpen = () => typeof document !== "undefined" && !!document.querySelector(".vti");
  lib.ac3dMount = (root = document) => {
    sweep();
    const covered = introOpen();
    root.querySelectorAll("[data-ac3d]").forEach((el) => {
      const underIntro = covered && !el.closest(".vti");
      if (underIntro) { if (el._ac && el._ac.hold) el._ac.hold(true); return; }
      if (el._ac && el._ac.hold) el._ac.hold(false);
      mountOne(el);
    });
  };

  lib.on("ac3d-force", (btn) => {
    const el = btn.closest("[data-ac3d]"); if (!el) return;
    const v = st(el.dataset.ac3dKey); v.force = true; v.canForce = false; v.note = ""; lib.storage.set("ac3d-force", true);   // remembered for this browser
    const stage = el.querySelector(".ac3d-stage"); stage.innerHTML = ""; mark(el, "is-fallback", false);
    lib.store.emit(); mountOne(el);
  });
  const ctlOf = (btn) => { const el = btn.closest("[data-ac3d]"); return el && el._ac ? [el, el._ac] : [el, null]; };
  lib.on("ac3d-preset", (btn) => { const [el, c] = ctlOf(btn); if (!el) return; st(el.dataset.ac3dKey).view = btn.dataset.v; if (c) c.preset(btn.dataset.v); lib.store.emit(); });
  lib.on("ac3d-reset", (btn) => { const [el, c] = ctlOf(btn); if (!el) return; st(el.dataset.ac3dKey).view = "three_quarter"; if (c) c.preset("three_quarter"); lib.store.emit(); });
  lib.on("ac3d-pause", (btn) => {
    const [el, c] = ctlOf(btn); if (!el) return;
    const v = st(el.dataset.ac3dKey), cur = v.paused == null ? reducedMotion() : v.paused;
    v.paused = !cur; if (c) c.setPaused(v.paused); lib.store.emit();
  });
})(V.lib);
