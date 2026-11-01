/* ==========================================================================
   lib/brand.js — Visiontra identity (mark + custom wordmark), pure SVG
   ========================================================================== */
/* VisionTra brand mark (Phase 13): an airliner planform seen from above, inside an inspection reticle —
   corner brackets and a range ring — on a dark instrument tile. */
(function (root) {
  root.brandMark = (size = 36, opts = {}) => {
    const u = "vm" + size + (opts.tile === false ? "n" : "");
    const ac = "M20 6.2c.9 0 1.45 1.1 1.45 2.7v6.9l9.6 5.1v1.9l-9.6-2.9v6.3l3.1 2.3v1.5l-4.55-1.15-4.55 1.15v-1.5l3.1-2.3v-6.3l-9.6 2.9v-1.9l9.6-5.1V8.9c0-1.6.55-2.7 1.45-2.7Z";
    return `<svg width="${size}" height="${size}" viewBox="0 0 40 40" fill="none" role="img" aria-label="VisionTra">
  <defs>
    <radialGradient id="${u}t" cx="50%" cy="40%" r="80%"><stop offset="0" stop-color="#1A1D24"/><stop offset=".6" stop-color="#0E1014"/><stop offset="1" stop-color="#060709"/></radialGradient>
    <linearGradient id="${u}r" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#F3E7C9"/><stop offset=".5" stop-color="#A88D55"/><stop offset="1" stop-color="#EBD9AE"/></linearGradient>
    <linearGradient id="${u}a" x1="20" y1="6" x2="20" y2="33" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#F4F6F9"/><stop offset="1" stop-color="#AEB7C3"/></linearGradient>
  </defs>
  ${opts.tile === false ? "" : `<rect class="vm-tile" x=".75" y=".75" width="38.5" height="38.5" rx="9" fill="url(#${u}t)" stroke="url(#${u}r)" stroke-width="1.2"/>`}
  <g stroke="#D8C08C" stroke-width="1.3" stroke-linecap="round" opacity=".9"><path d="M6 11V6h5M29 6h5v5M34 29v5h-5M11 34H6v-5"/></g>
  <circle cx="20" cy="20" r="11.5" stroke="#4FD8F0" stroke-opacity=".32" stroke-width=".7" stroke-dasharray="1.2 1.8"/>
  <path class="vm-ac" d="${ac}" fill="url(#${u}a)"/>
  <circle class="vm-ping" cx="27.4" cy="21.2" r="1.5" fill="#E5243F"/>
</svg>`;
  };

  /* ---- custom wordmark: extended geometric, monoline, flat-cut terminals ----
     V echoes the mark (one arm broken into data), O is a lens with a pupil, A is a crossbar-less Λ holding the spark. */
  const SW = 3.3, h = SW / 2, H = 20, GAP = 6.2;
  const G = {
    V: { w: 18, d: `M0 0L9 20L12.35 12.55M13.45 10.1L18 0` },
    I: { w: SW, d: `M${h} 0V20` },
    S: { w: 15, d: `M14.4 ${h + 2.6}C13 ${h + 0.2} 10.8 ${h} 7.6 ${h}C4.1 ${h} ${h} 2.6 ${h} 5.6C${h} 8.7 4.5 9.4 7.6 10C10.9 10.6 ${15 - h} 11.4 ${15 - h} 14.4C${15 - h} 17.4 11.1 ${20 - h} 7.4 ${20 - h}C4.2 ${20 - h} 1.8 ${19.5 - h} 0.3 ${17.2 - h}` },
    O: { w: 21, d: `M10.5 ${h}A${10 - h} ${10 - h} 0 1 1 10.49 ${h}Z`, extra: (x, u, acc) => `<circle cx="${x + 10.5}" cy="10" r="2.3" fill="${acc}"/>` },
    N: { w: 17, d: `M${h} 20V0L${17 - h} 20V0` },
    T: { w: 17, d: `M0 ${h}H17M8.5 ${h}V20` },
    R: { w: 16.5, d: `M${h} 20V${h}H9.2C12.8 ${h} ${16.5 - h - 0.4} 3.4 ${16.5 - h - 0.4} 6.6S12.8 ${11.6} 9.2 11.6H${h}M8.6 11.6L16.2 20` },
    A: { w: 18, d: "M0 20L9 0L18 20", extra: (x, u, acc) => `<path transform="translate(${x + 9} 13.4) scale(.8)" d="M0 -2.4c.25 1.6.7 2.05 2.3 2.4-1.6.3-2.05.75-2.3 2.4-.25-1.65-.7-2.1-2.3-2.4 1.6-.35 2.05-.8 2.3-2.4Z" fill="${acc}"/>` },
  };
  root.brandWordmark = (height = 16, opts = {}) => {
    const u = "vw" + String(height).replace(".", "_") + (opts.ink ? "i" : ""), word = "VISIONTRA";
    let x = 0, body = "";
    const acc = `url(#${u}a)`;
    for (const ch of word) { const g = G[ch]; body += `<path d="${g.d}" transform="translate(${x} 0)"/>`; if (g.extra) body += `<g stroke="none">${g.extra(x, u, acc)}</g>`; x += g.w + GAP; }
    const W = x - GAP;
    return `<svg height="${height}" viewBox="0 0 ${W} ${H}" width="${(height * W / H).toFixed(1)}" fill="none" role="img" aria-label="Visiontra" class="wordmark">
  <defs>
    <linearGradient id="${u}f" x1="0" y1="0" x2="${W}" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${opts.ink || "currentColor"}"/><stop offset=".62" stop-color="${opts.ink || "currentColor"}"/><stop offset=".82" stop-color="#D8C08C"/><stop offset="1" stop-color="#EBD9AE"/></linearGradient>
    <linearGradient id="${u}a" x1="0" y1="0" x2="${W}" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#D8C08C"/><stop offset="1" stop-color="#E5243F"/></linearGradient>
    <clipPath id="${u}c"><rect x="-2" y="0" width="${W + 4}" height="${H}"/></clipPath>
  </defs>
  <g clip-path="url(#${u}c)" stroke="url(#${u}f)" stroke-width="${SW}" stroke-linejoin="miter" stroke-miterlimit="10">${body}</g>
</svg>`;
  };
})(V.lib);
