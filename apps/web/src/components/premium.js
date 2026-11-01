/* ==========================================================================
   components/premium.js — Visiontra premium design-system primitives
   Thin, reusable builders over the token system (styles/tokens.css + luxe.css).

     PremiumButton        ui.btn({ variant: "primary" | "glass" | "ghost" })
     GlassCard            ui.glassCard({...})   (alias of ui.card with the glass surface)
     MetricCard           ui.metric({...})
     NavigationItem       .nav-item (app/shell.js)
     SectionLabel         ui.sectionLabel(text, { dot })
     StatusIndicator      ui.statusDot(tone, { pulse })
     PremiumIconContainer ui.iconBox(icon, { cat, size })
     WorkflowCard         .wf-card (features/overview.js)
     CommandSearch        .search-trigger (app/shell.js)
     VisionCore           <canvas data-fx="core"> → lib.core3d (lib/fx3d.js)

   Category colours carry meaning and are never decorative:
     gold = primary / premium · cyan = vision & live processing · azure = data ·
     violet = models · emerald = success · magenta = experiments
   ========================================================================== */
(function (ui, lib) {
  const esc = lib.esc, I = lib.icon;
  ui.CATEGORIES = ["gold", "cyan", "azure", "violet", "emerald", "magenta"];
  ui.sectionLabel = (text, { dot = "" } = {}) => `<span class="section-label">${dot ? ui.statusDot(dot) : ""}${esc(text)}</span>`;
  /** tone: gold | cyan | emerald | violet | warn | bad | idle — always paired with text by callers (never colour alone). */
  ui.statusDot = (tone = "gold", { pulse = false, label } = {}) => `<span class="status-dot ${tone} ${pulse ? "pulse-dot" : ""}" ${label ? `role="img" aria-label="${esc(label)}"` : 'aria-hidden="true"'}></span>`;
  ui.iconBox = (icon, { cat = "gold", size = "" } = {}) => `<span class="pic ${size} cat-${cat}" aria-hidden="true">${I(icon)}</span>`;
  ui.glassCard = (o) => ui.card({ ...o, cls: `glass ${o.cls || ""}` });
  ui.premiumButton = (o) => ui.btn({ variant: "primary", ...o });
})(V.ui, V.lib);
