/* ==========================================================================
   lib/aero.js — aerospace visuals: aircraft planform line art and synthetic
   SAMPLE panel illustrations. The panels are procedural drawings used only by
   clearly badged sample records; they are not photographs and not model input.
   ========================================================================== */
(function (lib) {
  const esc = lib.esc;

  /** Deterministic PRNG so sample illustrations are identical on every load. */
  lib.rng = (seed) => { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; };

  /* Top-down airliner planform, nose up, viewBox 0 0 400 400. */
  const AIRFRAME = "M200 18C208 18 213 30 213 48L213 168 370 248 370 262 213 222 213 330 262 362 262 374 213 362 208 380 200 386 192 380 187 362 138 374 138 362 187 330 187 222 30 262 30 248 187 168 187 48C187 30 192 18 200 18Z";
  const ENGINES = '<rect x="128" y="186" width="14" height="40" rx="7"/><rect x="258" y="186" width="14" height="40" rx="7"/>';
  /** Inspection zones on the planform (used by the welcome visual and the component picker hint). */
  lib.AERO_ZONES = {
    fuselage: { x: 200, y: 120 }, wing_upper: { x: 290, y: 225 }, wing_lower: { x: 110, y: 225 }, empennage: { x: 200, y: 352 },
    nacelle: { x: 265, y: 206 }, control_surface: { x: 340, y: 256 }, landing_gear: { x: 200, y: 205 }, other: { x: 200, y: 60 },
  };

  /**
   * Aircraft planform line art with an optional scanning beam.
   * opts: { size, scan:boolean, zones:[ids], cls, label }
   */
  lib.aircraft = ({ size = 320, scan = true, zones = [], cls = "", label } = {}) => {
    const id = "ac" + Math.random().toString(36).slice(2, 7);
    const z = zones.map((k) => lib.AERO_ZONES[k]).filter(Boolean)
      .map((p, i) => `<g class="ac-zone" style="--i:${i}"><circle cx="${p.x}" cy="${p.y}" r="9"/><circle cx="${p.x}" cy="${p.y}" r="2.6" class="ac-zone-core"/></g>`).join("");
    return `<svg class="aircraft ${cls}" width="${size}" height="${size}" viewBox="0 0 400 400" fill="none" ${label ? `role="img" aria-label="${esc(label)}"` : 'aria-hidden="true"'}>
      <defs>
        <linearGradient id="${id}-s" x1="0" y1="0" x2="400" y2="400" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="var(--aero-cyan, #45D9FF)"/><stop offset="1" stop-color="var(--aero-violet, #8B7CFF)"/></linearGradient>
        <linearGradient id="${id}-b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--aero-cyan, #45D9FF)" stop-opacity="0"/><stop offset=".85" stop-color="var(--aero-cyan, #45D9FF)" stop-opacity=".28"/><stop offset="1" stop-color="#fff" stop-opacity=".9"/></linearGradient>
        <clipPath id="${id}-c"><path d="${AIRFRAME}"/>${ENGINES}</clipPath>
        <pattern id="${id}-g" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M12 0H0V12" stroke="var(--aero-cyan, #45D9FF)" stroke-opacity=".12" stroke-width=".6"/></pattern>
      </defs>
      <g clip-path="url(#${id}-c)"><rect width="400" height="400" fill="url(#${id}-g)"/>${scan ? `<rect class="ac-beam" x="0" y="-60" width="400" height="60" fill="url(#${id}-b)"/>` : ""}</g>
      <path class="ac-outline" d="${AIRFRAME}" stroke="url(#${id}-s)" stroke-width="1.6" stroke-linejoin="round"/>
      <g class="ac-outline" stroke="url(#${id}-s)" stroke-width="1.2">${ENGINES}</g>
      <path d="M200 40V370M187 168 213 168M187 222H213" stroke="var(--aero-cyan, #45D9FF)" stroke-opacity=".25" stroke-width=".7" stroke-dasharray="2 4"/>
      ${z}
    </svg>`;
  };

  /* ---------- synthetic sample panel (SVG) ---------- */
  const COMP_TINT = { fuselage: ["#C9D0D8", "#9BA4AE"], wing_upper: ["#BCC4CD", "#8C96A1"], wing_lower: ["#B5BDC6", "#86909B"], empennage: ["#D4DAE0", "#A2ABB4"], nacelle: ["#C0C7CE", "#7F8994"], control_surface: ["#C6CDD4", "#959FA9"], landing_gear: ["#A8B0B8", "#6F7983"], other: ["#C2C9D0", "#8F99A3"] };

  function crack(r, b, W, H) {
    const x0 = b.x * W, y0 = b.y * H, w = b.w * W, h = b.h * H;
    const horiz = w >= h, n = 9; let pts = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      pts.push(horiz ? [x0 + t * w, y0 + h / 2 + (r() - 0.5) * h * 0.7] : [x0 + w / 2 + (r() - 0.5) * w * 0.7, y0 + t * h]);
    }
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
    const br = pts[Math.floor(n / 2)], bd = `M${br[0].toFixed(1)} ${br[1].toFixed(1)}l${(horiz ? 1 : 1) * (12 + r() * 18)} ${(horiz ? -1 : 1) * (14 + r() * 16)}`;
    return `<path d="${d}" stroke="#1D2228" stroke-width="3.2" fill="none" stroke-linejoin="round"/><path d="${d}" stroke="#F4F7FA" stroke-opacity=".55" stroke-width="1" fill="none" transform="translate(1.4 1.4)"/><path d="${bd}" stroke="#1D2228" stroke-width="1.8" fill="none"/>`;
  }
  function dent(r, b, W, H, k) {
    const cx = (b.x + b.w / 2) * W, cy = (b.y + b.h / 2) * H, rx = (b.w * W) / 2.3, ry = (b.h * H) / 2.3;
    return `<radialGradient id="dg${k}" cx="58%" cy="60%" r="60%"><stop offset="0" stop-color="#3B434C" stop-opacity=".55"/><stop offset=".55" stop-color="#5E6770" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <radialGradient id="dh${k}" cx="35%" cy="32%" r="55%"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="url(#dg${k})"/><ellipse cx="${cx - rx * 0.18}" cy="${cy - ry * 0.2}" rx="${rx * 0.75}" ry="${ry * 0.7}" fill="url(#dh${k})"/>`;
  }
  function corrosion(r, b, W, H) {
    let s = "";
    const cx = (b.x + b.w / 2) * W, cy = (b.y + b.h / 2) * H;
    for (let i = 0; i < 90; i++) {
      const a = r() * Math.PI * 2, d = Math.sqrt(r()), x = cx + Math.cos(a) * d * b.w * W * 0.45, y = cy + Math.sin(a) * d * b.h * H * 0.45;
      const c = r() < 0.55 ? `rgba(${120 + (r() * 50) | 0},${72 + (r() * 30) | 0},${32 + (r() * 20) | 0},${0.35 + r() * 0.45})` : `rgba(238,232,214,${0.35 + r() * 0.4})`;
      s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(1.2 + r() * 4.2).toFixed(1)}" fill="${c}"/>`;
    }
    return `<g filter="url(#soft)">${s}</g>`;
  }

  /**
   * Procedural aluminium-panel illustration. spec: { seed, component, defects:[{category, box}], w, h }
   * Returns an SVG string; always carries a visible "SAMPLE · synthetic illustration" mark.
   */
  lib.panelSVG = ({ seed = 1, component = "fuselage", defects = [], w = 1600, h = 1000 } = {}) => {
    const r = lib.rng(seed), [c1, c2] = COMP_TINT[component] || COMP_TINT.other;
    let seams = "", rivets = "";
    const cols = 3 + Math.floor(r() * 2), rows = 2 + Math.floor(r() * 2);
    for (let i = 1; i < cols; i++) { const x = (i / cols) * w + (r() - 0.5) * 30; seams += `<path d="M${x.toFixed(0)} 0V${h}" stroke="#5B636C" stroke-opacity=".55" stroke-width="2.2"/><path d="M${(x + 2).toFixed(0)} 0V${h}" stroke="#fff" stroke-opacity=".35" stroke-width="1"/>`; for (let y = 24; y < h; y += 38) rivets += `<circle cx="${(x - 16).toFixed(0)}" cy="${y}" r="4.2"/><circle cx="${(x + 18).toFixed(0)}" cy="${y + 19}" r="4.2"/>`; }
    for (let j = 1; j < rows; j++) { const y = (j / rows) * h + (r() - 0.5) * 24; seams += `<path d="M0 ${y.toFixed(0)}H${w}" stroke="#5B636C" stroke-opacity=".5" stroke-width="2"/><path d="M0 ${(y + 2).toFixed(0)}H${w}" stroke="#fff" stroke-opacity=".32" stroke-width="1"/>`; for (let x = 22; x < w; x += 40) rivets += `<circle cx="${x}" cy="${(y - 15).toFixed(0)}" r="4"/>`; }
    const marks = defects.map((d, k) => d.category === "crack" ? crack(r, d.box, w, h) : d.category === "dent" ? dent(r, d.box, w, h, k) : d.category === "corrosion" ? corrosion(r, d.box, w, h) : `<rect x="${d.box.x * w}" y="${d.box.y * h}" width="${d.box.w * w}" height="${d.box.h * h}" rx="10" fill="#2B3138" fill-opacity=".18"/>`).join("");
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<defs><linearGradient id="m" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset=".5" stop-color="${c2}"/><stop offset="1" stop-color="${c1}"/></linearGradient>
<filter id="brush" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".002 .35" numOctaves="2" seed="${seed}"/><feColorMatrix values="0 0 0 0 .5  0 0 0 0 .5  0 0 0 0 .5  0 0 0 .22 0"/></filter>
<filter id="soft"><feGaussianBlur stdDeviation=".6"/></filter>
<radialGradient id="sheen" cx="${(20 + r() * 60).toFixed(0)}%" cy="10%" r="80%"><stop offset="0" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
<rect width="${w}" height="${h}" fill="url(#m)"/><rect width="${w}" height="${h}" filter="url(#brush)"/><rect width="${w}" height="${h}" fill="url(#sheen)"/>
${seams}<g fill="#7C858F" stroke="#E9EDF1" stroke-opacity=".6" stroke-width="1">${rivets}</g>${marks}
<g font-family="Helvetica, Arial, sans-serif" font-size="22" font-weight="700"><rect x="${w - 430}" y="${h - 58}" width="410" height="40" rx="8" fill="#0B1222" fill-opacity=".72"/><text x="${w - 412}" y="${h - 31}" fill="#FFB020">SAMPLE</text><text x="${w - 318}" y="${h - 31}" fill="#E8EEF6" font-weight="400">· synthetic illustration</text></g>
</svg>`;
  };
  lib.svgDataUrl = (svg) => "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
})(V.lib);
