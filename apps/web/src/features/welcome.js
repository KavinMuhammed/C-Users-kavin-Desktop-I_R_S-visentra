/* ==========================================================================
   features/welcome.js — public product page (/welcome)
   ========================================================================== */
(function (F, ui, lib) {
  const esc = lib.esc, I = lib.icon;
  const STEPS = [
    ["upload", "Upload", "Aircraft surface photos or drone frames. Each image becomes a saved inspection."],
    ["scan", "Detect", "A YOLOX detector trained on aircraft-defect datasets locates cracks, dents and corrosion."],
    ["check_square", "Review", "An engineer confirms, rejects or reclassifies every detection and marks anything missed."],
    ["scroll", "Report", "Sign-off produces a PDF with the image, boxes, decisions, model and checksums."],
  ];
  F.welcome = {
    page() {
      const signed = !!(V.app && V.app.user);
      return `<div class="welcome">
        <header class="wl-top"><a class="row" href="#/welcome" style="gap:10px;text-decoration:none" aria-label="VisionTra home">${F.auth.logo(36)}<span class="col" style="gap:0"><span class="brand-name">${lib.brandWordmark(13)}</span><span class="brand-sub">Aircraft surface inspection</span></span></a><span class="grow"></span>
          ${signed ? ui.btn({ label: "Inspection Center", iconRight: "chev_r", variant: "primary", act: "nav", data: { to: "/inspection" } }) : `${ui.btn({ label: "Sign in", variant: "ghost", act: "nav", data: { to: "/login" } })}${ui.btn({ label: "Create account", variant: "primary", act: "nav", data: { to: "/register" } })}`}</header>
        <main id="main" class="wl-main" tabindex="-1">
          <section class="wl-hero">
            <div class="col gap-5 wl-copy">
              <div class="eyebrow">${I("scan")} Aerospace &amp; defence · computer vision</div>
              <h1 class="wl-title">Smarter visual inspection <span>of aircraft.</span></h1>
              <p class="wl-lead">Detect, classify and document dents, cracks and corrosion on aircraft surfaces from images or drone footage — with the evidence on the image and an engineer's decision on every finding.</p>
              <div class="row wrap gap-3">${ui.btn({ label: signed ? "New inspection" : "Get started", icon: "upload", variant: "primary", size: "lg", act: "nav", data: { to: signed ? "/inspection/new" : "/register" } })}${signed ? "" : ui.btn({ label: "Sign in", size: "lg", act: "nav", data: { to: "/login" } })}${ui.btn({ label: "Play introduction", icon: "play", size: "lg", variant: "ghost", act: "intro-replay" })}</div>
            </div>
            <div class="wl-visual">${lib.ac3dView({ key: "welcome", mode: "hero", zones: {}, label: "VisionTra 3D aircraft", legend: false, cls: "wl-ac3d" })}</div>
          </section>
          <section class="wl-section" aria-labelledby="wl-flow"><h2 id="wl-flow" class="section-label">How it works</h2>
            <ol class="wl-steps">${STEPS.map(([ic, t, d], i) => `<li style="--i:${i}"><span class="wl-step-n">${String(i + 1).padStart(2, "0")}</span><span class="pic cat-gold">${I(ic)}</span><strong>${t}</strong><span class="xs muted">${d}</span></li>`).join("")}</ol></section>
          <footer class="wl-foot xs muted">VisionTra is decision support for qualified maintenance engineers. It does not determine airworthiness.</footer>
        </main></div>`;
    },
  };
})(V.features, V.ui, V.lib);
