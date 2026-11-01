/* ==========================================================================
   features/intro.js — the VisionTra introduction (Phase 15)

   Obsidian Nexus (26 Oct): a ~5 s sequence shown once per browser, in five acts — void, signal (gold field, violet
   sweep), airframe (the real reference aircraft), identity (wordmark), entry. It plays immediately; the 3D views on
   the page underneath are held until it closes (lib/ac3d.js), which removed the measured main-thread freeze.
   · Skip is visible from the first frame; Esc also skips. Enter Inspection Center appears at the end.
   · Seen state lives in this browser (lib.storage "intro-v1"); replay from Settings or the product page.
   · prefers-reduced-motion (or the app's reduced-motion setting): everything is shown at once, no animation.
   · A synthesized soundtrack (Web Audio, no files). Sound is OFF by default with a visible toggle (remembered); when
     it is on, a "Start" gate appears because browsers only allow audio after a click. It never plays without one.
   · No metrics, customers or certifications — the statement only describes what the product does.
   · Nothing waits for it: the 3D view loads lazily behind it and falls back to the wireframe; the app renders
     underneath, so closing it never needs a reload. A broken intro is hidden instead of blocking the app.
   ========================================================================== */
(function (F, ui, lib) {
  const esc = lib.esc, I = lib.icon;
  const KEY = "intro-v1";
  const st = { open: null, playing: false, t0: 0, phase: "gate", go: false };
  const soundOn = () => { try { return lib.storage.get("intro-sound", false) === true; } catch { return false; } };

  /* ---------- synthesized soundtrack (Web Audio) ---------- */
  let AC = null, master = null, nodes = [];
  function stopSound() { nodes.forEach((n) => { try { n.stop && n.stop(); } catch {} }); nodes = []; if (master) { try { master.gain.cancelScheduledValues(0); master.gain.setTargetAtTime(0, AC.currentTime, 0.08); } catch {} } }
  function playSound() {
    try {
      AC = AC || new (window.AudioContext || window.webkitAudioContext)();
      AC.resume && AC.resume();
      const t = AC.currentTime + 0.05;
      const comp = AC.createDynamicsCompressor(); comp.connect(AC.destination);
      master = AC.createGain(); master.gain.value = 0.55; master.connect(comp);
      const env = (g, a, peak, hold, rel, at) => { g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(peak, at + a); g.gain.setValueAtTime(peak, at + a + hold); g.gain.exponentialRampToValueAtTime(0.0001, at + a + hold + rel); };
      const osc = (type, f, at, dur, peak, a = 0.02, rel = 0.4, dest = master) => { const o = AC.createOscillator(), g = AC.createGain(); o.type = type; o.frequency.setValueAtTime(f, at); o.connect(g); g.connect(dest); env(g, a, peak, Math.max(0, dur - a - rel), rel, at); o.start(at); o.stop(at + dur + 0.05); nodes.push(o); return o; };
      // 1. sub-bass swell + airy noise (the hangar)
      const sub = osc("sine", 46, t, 4.6, 0.32, 1.6, 1.8); sub.frequency.exponentialRampToValueAtTime(62, t + 2.6);
      const len = AC.sampleRate * 3, buf = AC.createBuffer(1, len, AC.sampleRate), ch = buf.getChannelData(0);
      for (let i = 0; i < len; i++) ch[i] = (Math.random() * 2 - 1) * 0.6;
      const nz = AC.createBufferSource(), lp = AC.createBiquadFilter(), ng = AC.createGain(); nz.buffer = buf; lp.type = "bandpass"; lp.Q.value = 0.7;
      lp.frequency.setValueAtTime(300, t); lp.frequency.exponentialRampToValueAtTime(2400, t + 2.2); lp.frequency.exponentialRampToValueAtTime(500, t + 3);
      nz.connect(lp); lp.connect(ng); ng.connect(master); env(ng, 1.2, 0.09, 0.6, 1.1, t); nz.start(t); nz.stop(t + 3.1); nodes.push(nz);
      // 2. gold shimmer on the wordmark (detuned fifth + octave)
      [[392, 0], [587.3, 0.06], [784, 0.12], [1174.7, 0.2]].forEach(([f, d]) => { const o = osc("sine", f, t + 0.5 + d, 2.6, 0.07, 0.25, 1.9); o.detune.value = (Math.random() - 0.5) * 12; });
      [2637, 3136, 3951].forEach((f, k) => osc("triangle", f, t + 0.7 + k * 0.11, 0.5, 0.025, 0.005, 0.45));
      // 3. scan sweep (cyan → crimson)
      const sw = AC.createOscillator(), bp = AC.createBiquadFilter(), sg = AC.createGain(); sw.type = "sawtooth"; sw.frequency.value = 110; bp.type = "bandpass"; bp.Q.value = 9;
      bp.frequency.setValueAtTime(260, t + 2.0); bp.frequency.exponentialRampToValueAtTime(2600, t + 3.3); sw.connect(bp); bp.connect(sg); sg.connect(master);
      env(sg, 0.15, 0.06, 0.9, 0.35, t + 2.0); sw.start(t + 2.0); sw.stop(t + 3.5); nodes.push(sw);
      // 4. lock-on chime
      osc("sine", 1318.5, t + 3.05, 0.14, 0.09, 0.005, 0.12); osc("sine", 1760, t + 3.22, 0.5, 0.08, 0.005, 0.45);
    } catch (e) { /* audio is optional */ }
  }

  const reduced = () => (lib.motion && lib.motion.reduced && lib.motion.reduced()) || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const seen = () => { try { return !!lib.storage.get(KEY, false); } catch { return true; } };
  const markSeen = () => { try { lib.storage.set(KEY, true); } catch {} };
  /* The timeline waits (in act I's darkness, Skip live) until the 3D view has drawn its first frame, at most 2 s:
     starting the GPU and the first draw are unavoidable main-thread work, so it happens before anything moves. */
  let goTimer = 0;
  const go = () => { clearTimeout(goTimer); st.go = true; const r = document.querySelector(".vti"); r && r.classList.add("is-go"); };
  const arm = () => { st.go = false; clearTimeout(goTimer); goTimer = setTimeout(go, 2000); };
  document.addEventListener("ac3d-ready", (e) => { if (st.open && e.target.closest && e.target.closest(".vti")) go(); });

  F.intro = {
    /** True when the intro should cover the page now. First visit only, never on password-reset links. */
    active() {
      if (st.open === null) {
        st.open = !seen() && !/^#\/reset\//.test(location.hash) && !/[?&]nointro\b/.test(location.search);
        // a modal dialog takes focus: Start when the sound gate is shown, otherwise Skip
        if (st.open) { st.t0 = Date.now(); arm(); setTimeout(() => { const b = document.querySelector(".vti [data-act=intro-start]") || document.querySelector(".vti [data-act=intro-skip]"); b && b.focus(); }, 60); }
      }
      return st.open;
    },
    open(play) { st.open = true; st.t0 = Date.now(); st.phase = play ? "play" : "gate"; arm(); lib.store.emit(); if (play && soundOn() && !reduced()) playSound();
      setTimeout(() => { const b = document.querySelector(".vti [data-act=intro-start]") || document.querySelector(".vti [data-act=intro-skip]"); b && b.focus(); }, 60); },
    start() { st.phase = "play"; st.t0 = Date.now(); if (soundOn() && !reduced()) playSound(); lib.store.emit(); },
    close(to) {
      stopSound();
      markSeen(); st.open = false;
      if (to) lib.router.go(to); else lib.store.emit();
      setTimeout(() => { const m = document.getElementById("main") || document.querySelector("main"); m && m.focus && m.focus({ preventScroll: true }); }, 30);
    },
    /* Obsidian Nexus sequence (~5 s, CSS-timed, Skip/Esc live from the first frame):
       I void (obsidian depth, distant stars) · II signal (gold field resolves, violet sweep) · III airframe (the real
       reference aircraft dollies in) · IV identity (wordmark, descriptor) · V entry (statement, Enter).
       It plays immediately; the Start gate only exists when the user has turned sound on (browsers require a click
       before audio). The act rail is decoration, never measured data. */
    view() {
      const signed = !!(V.app && V.app.user);
      const rm = reduced(), snd = soundOn(), gate = !rm && snd && st.phase === "gate";
      const word = "VISIONTRA".split("").map((c, i) => `<span style="--i:${i}">${c}</span>`).join("");
      const acts = ["Void", "Signal", "Airframe", "Identity", "Entry"];
      return `<div class="vti vtx ${rm ? "is-static" : gate ? "is-gate" : "is-playing"}${st.go ? " is-go" : ""}" role="dialog" aria-modal="true" aria-labelledby="intro-title" aria-describedby="intro-statement" data-key="intro">
        <div class="vtx-void" aria-hidden="true"><i class="vtx-nebula"></i><i class="vtx-haze"></i><i class="vtx-floor"></i></div>
        <div class="vtx-stage" aria-hidden="true">${lib.ac3dView({ key: "intro", mode: "hero", zones: {}, label: "VisionTra 3D aircraft", legend: false, cls: "vti-ac vtx-ac" })}</div>
        <i class="vtx-sweep" aria-hidden="true"></i><i class="vtx-vignette" aria-hidden="true"></i>
        <div class="vtx-topbar">
          <button type="button" class="vtx-btn" data-act="intro-sound" aria-pressed="${snd}" title="${snd ? "Sound on" : "Sound off"}">${I(snd ? "volume" : "mute")}<span>${snd ? "Sound on" : "Sound off"}</span></button>
          <button type="button" class="vtx-btn" data-act="intro-skip">Skip intro <kbd>Esc</kbd></button></div>
        ${gate ? `<div class="vtx-gate"><div class="vtx-gate-word">VISIONTRA</div>
          <button type="button" class="vtx-start" data-act="intro-start" id="intro-start">${I("play")}<span>Start with sound</span></button>
          <p class="vtx-gate-hint">Aircraft Surface Inspection · 5 seconds</p></div>` : ""}
        <div class="vtx-copy">
          <p class="vtx-eyebrow">Aircraft surface inspection</p>
          <h1 class="vtx-word" id="intro-title" aria-label="VisionTra">${word}</h1>
          <p class="vtx-desc">Aerospace Forensics</p>
          <p class="vtx-tag"><span>Identify.</span> <span>Review.</span> <span>Document.</span></p>
          <p class="vtx-statement" id="intro-statement">Aircraft imagery. Traceable evidence. Engineer judgment. Review AI candidates and document the findings that matter.</p>
          <div class="vtx-actions">${ui.btn({ label: signed ? "Enter Inspection Center" : "Sign in to the Inspection Center", iconRight: "chev_r", variant: "primary", size: "lg", act: "intro-enter", id: "intro-enter" })}</div>
          <p class="vtx-foot">Decision support for qualified engineers — not an airworthiness determination.</p>
        </div>
        <ol class="vtx-acts" aria-hidden="true">${acts.map((a, i) => `<li style="--i:${i}"><b>${["I", "II", "III", "IV", "V"][i]}</b>${a}</li>`).join("")}</ol>
      </div>`;
    },
  };

  lib.on("intro-skip", () => F.intro.close());
  lib.on("intro-enter", () => F.intro.close(V.app && V.app.user ? "/inspection" : "/login"));
  lib.on("intro-replay", () => F.intro.open(true));
  lib.on("intro-start", () => F.intro.start());
  lib.on("intro-sound", () => { const on = !soundOn(); try { lib.storage.set("intro-sound", on); } catch {} if (!on) stopSound(); else if (st.phase === "play" && Date.now() - st.t0 < 1500) playSound(); lib.store.emit(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && st.open) { e.preventDefault(); F.intro.close(); } }, true);
  // a broken intro must never trap the user: if rendering throws, it is closed and marked seen
  F.intro.safeView = () => { try { return F.intro.view(); } catch (e) { console.error("intro failed", e); markSeen(); st.open = false; return ""; } };
})(V.features, V.ui, V.lib);
