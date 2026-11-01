/* ==========================================================================
   lib/aircraft3d.js — a 3D wireframe airliner rendered on canvas (no libraries).
   Gold neon airframe, a crimson inspection scan that sweeps nose to tail, and
   pulsing defect markers on the zones where defects were confirmed.

   <canvas data-fx="aircraft3d" data-zones='{"fuselage":3,"wing_upper":1}' data-mode="hero|map|mini" data-focus="nacelle">
   Starts from lib.fx3dMount(). Pauses off-screen and in hidden tabs; with reduced motion it draws one still frame.
   ========================================================================== */
(function (lib) {
  const GOLD = [245, 198, 107], GOLD2 = [255, 225, 160], RED = [255, 46, 77], CYAN = [69, 217, 255];
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

  /* ---------- airframe geometry (units ≈ metres / 4; x = nose → tail, y = span, z = up) ---------- */
  function build() {
    const L = [];                                   // line segments [[x,y,z],[x,y,z], part]
    const seg = (a, b, part) => L.push([a, b, part]);
    const R = (x) => {                              // fuselage radius profile
      if (x > 9) return 1.15 * Math.sqrt(Math.max(0, 1 - ((x - 9) / 2.6) ** 2));          // nose
      if (x < -7) return 1.15 * Math.max(0.12, 1 - ((-7 - x) / 4.2));                       // tail cone
      return 1.15;
    };
    const XS = [11.5, 11.2, 10.6, 9.8, 9, 7, 5, 3, 1, -1, -3, -5, -7, -8.5, -10, -11.2];
    const N = 14;
    const ring = (x) => Array.from({ length: N }, (_, k) => { const a = (k / N) * Math.PI * 2, r = R(x); return [x, Math.cos(a) * r, Math.sin(a) * r * 1.05 + (x < -7 ? (-7 - x) * 0.12 : 0)]; });
    const rings = XS.map(ring);
    rings.forEach((rg, i) => { for (let k = 0; k < N; k++) { seg(rg[k], rg[(k + 1) % N], "fuselage"); if (i) seg(rings[i - 1][k], rg[k], "fuselage"); } });
    // cockpit windows
    for (let k = -2; k <= 2; k++) seg([10.2, k * 0.22, 0.55], [10.2, (k + 0.8) * 0.22, 0.55], "fuselage");
    // wings (swept, dihedral): root chord x 2.2 → -2.6 at y ±1.1; tip chord x -4.6 → -6.0 at y ±12
    const wing = (s) => {
      const z = (y) => -0.55 + Math.abs(y) * 0.08;
      const root = [[2.2, 1.1 * s], [-2.6, 1.1 * s]], tip = [[-4.6, 12 * s], [-6.0, 12 * s]];
      const pts = [root[0], tip[0], tip[1], root[1]].map(([x, y]) => [x, y, z(y)]);
      for (let i = 0; i < 4; i++) seg(pts[i], pts[(i + 1) % 4], s > 0 ? "wing_upper" : "wing_lower");
      for (let t = 1; t < 6; t++) { const f = t / 6; const a = [2.2 + (-4.6 - 2.2) * f, (1.1 + 10.9 * f) * s], b = [-2.6 + (-6 + 2.6) * f, (1.1 + 10.9 * f) * s]; seg([a[0], a[1], z(a[1])], [b[0], b[1], z(b[1])], s > 0 ? "wing_upper" : "wing_lower"); }
      // aileron / flap line
      seg([-3.4, 3 * s, z(3)], [-5.6, 10.5 * s, z(10.5)], "control_surface");
      // engine nacelle under the wing
      const ex = -0.6, ey = 4.2 * s, ez = -1.35, er = 0.55, M = 10;
      const ec = (x) => Array.from({ length: M }, (_, k) => { const a = (k / M) * Math.PI * 2; return [x, ey + Math.cos(a) * er, ez + Math.sin(a) * er]; });
      const e1 = ec(ex + 1.6), e2 = ec(ex - 1.4);
      for (let k = 0; k < M; k++) { seg(e1[k], e1[(k + 1) % M], "nacelle"); seg(e2[k], e2[(k + 1) % M], "nacelle"); seg(e1[k], e2[k], "nacelle"); }
      seg([ex, ey, ez + er], [ex - 0.4, ey, z(ey)], "nacelle");
    };
    wing(1); wing(-1);
    // horizontal stabiliser
    [1, -1].forEach((s) => { const p = [[-8.4, 0.6 * s, 0.2], [-10.2, 4.4 * s, 0.5], [-11, 4.4 * s, 0.5], [-10.4, 0.5 * s, 0.25]]; for (let i = 0; i < 4; i++) seg(p[i], p[(i + 1) % 4], "empennage"); });
    // vertical fin
    const f = [[-8, 0, 1.1], [-10.6, 0, 4.6], [-11.6, 0, 4.6], [-11.2, 0, 1.0]];
    for (let i = 0; i < 4; i++) seg(f[i], f[(i + 1) % 4], "empennage");
    seg([-9.3, 0, 2.6], [-11.4, 0, 2.6], "empennage");
    // landing gear (stowed doors outline)
    seg([0.6, 0.7, -1.05], [-1.4, 0.7, -1.05], "landing_gear"); seg([0.6, -0.7, -1.05], [-1.4, -0.7, -1.05], "landing_gear");
    return L;
  }
  const GEOM = build();
  /** Where a defect marker sits for each component zone. */
  const ZONE_AT = { fuselage: [4, 0.8, 0.9], wing_upper: [-3.8, 7, -0.05], wing_lower: [-3.8, -7, -0.05], empennage: [-10.6, 0, 3.6], nacelle: [-0.6, 4.2, -1.35],
    control_surface: [-5, 9.5, 0.2], landing_gear: [-0.4, 0, -1.2] };   // no "other": an unspecified component is never placed
  lib.AIRCRAFT_ZONES = Object.keys(ZONE_AT);

  function rot([x, y, z], yaw, pitch, roll) {
    let c = Math.cos(yaw), s = Math.sin(yaw); [x, y] = [x * c - y * s, x * s + y * c];
    c = Math.cos(roll); s = Math.sin(roll); [y, z] = [y * c - z * s, y * s + z * c];
    c = Math.cos(pitch); s = Math.sin(pitch); [x, z] = [x * c - z * s, x * s + z * c];
    return [x, y, z];
  }

  lib.aircraft3d = (canvas) => {
    if (!canvas || canvas._ac3d) return;
    canvas._ac3d = true;
    const mode = canvas.dataset.mode || "hero";
    let zones = {}, zstr = null, focus = "";
    const refresh = () => {                          // the page re-renders the same canvas: pick up new data without restarting
      if (canvas.dataset.zones !== zstr) { zstr = canvas.dataset.zones; try { zones = JSON.parse(zstr || "{}"); } catch { zones = {}; } }
      focus = canvas.dataset.focus || "";
    };
    const ptr = lib.fxPointer || { sx: 0, sy: 0 };
    const draw = (g, W, H, t) => {
      refresh();
      g.clearRect(0, 0, W, H);
      const yaw = (mode === "mini" ? 0.6 : 0.35) + t * (mode === "map" ? 0.16 : 0.22) + ptr.sx * 0.25;
      const pitch = -0.62 + ptr.sy * 0.08 + Math.sin(t * 0.5) * 0.03;
      const roll = Math.sin(t * 0.7) * 0.05;
      const scale = Math.min(W, H) / (mode === "mini" ? 21 : 25), cx = W / 2, cy = H / 2 + (mode === "hero" ? H * 0.02 : 0), cam = 34;
      const P = (v) => { const [x, y, z] = rot(v, yaw, pitch, roll); const k = cam / (cam - x * 0.55); return [cx + y * scale * k, cy - z * scale * k - x * scale * 0.22 * k, x]; };
      // floor grid (hangar deck)
      g.lineWidth = 1;
      for (let i = -6; i <= 6; i++) {
        const a = P([i * 2.2, -14, -3.2]), b = P([i * 2.2, 14, -3.2]), c = P([-14, i * 2.2, -3.2]), d = P([14, i * 2.2, -3.2]);
        g.strokeStyle = rgba(GOLD, 0.05); g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.moveTo(c[0], c[1]); g.lineTo(d[0], d[1]); g.stroke();
      }
      // scan plane position along the fuselage
      const sx = 12 - ((t * 4.2) % 26);
      g.lineCap = "round";
      g.shadowBlur = mode === "mini" ? 4 : 10;
      for (const [a, b, part] of GEOM) {
        const pa = P(a), pb = P(b);
        const mid = (a[0] + b[0]) / 2, near = Math.max(0, 1 - Math.abs(mid - sx) / 1.6);
        const depth = 0.45 + 0.55 * Math.max(0, Math.min(1, (pa[2] + pb[2] + 24) / 48));
        const focusHit = focus && part === focus;
        const col = near > 0.05 ? [Math.round(GOLD[0] + (CYAN[0] - GOLD[0]) * near), Math.round(GOLD[1] + (CYAN[1] - GOLD[1]) * near), Math.round(GOLD[2] + (CYAN[2] - GOLD[2]) * near)]
          : focusHit ? CYAN : GOLD;
        g.strokeStyle = rgba(col, (focus && !focusHit ? 0.35 : 0.85) * depth);
        g.shadowColor = rgba(near > 0.05 || focusHit ? CYAN : GOLD, 0.8);   // the scan is instrumentation (cyan); crimson is reserved for confirmed defects
        g.lineWidth = (focusHit ? 1.7 : 1.1) * (mode === "mini" ? 0.9 : 1);
        g.beginPath(); g.moveTo(pa[0], pa[1]); g.lineTo(pb[0], pb[1]); g.stroke();
      }
      g.shadowBlur = 0;
      // scan plane outline
      if (mode !== "mini") {
        const q = [[sx, -13, -2.5], [sx, 13, -2.5], [sx, 13, 4.5], [sx, -13, 4.5]].map(P);
        const grd = g.createLinearGradient(q[0][0], q[0][1], q[2][0], q[2][1]);
        grd.addColorStop(0, rgba(CYAN, 0.0)); grd.addColorStop(0.5, rgba(CYAN, 0.08)); grd.addColorStop(1, rgba(CYAN, 0.0));
        g.fillStyle = grd; g.beginPath(); q.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]))); g.closePath(); g.fill();
      }
      // defect markers
      Object.entries(zones).forEach(([z, v], i) => {
        const conf = typeof v === "object" && v ? v.confirmed || 0 : v || 0, ai = typeof v === "object" && v ? v.ai || 0 : 0, n = conf + ai;
        if (!n || !ZONE_AT[z]) return;
        const C = conf ? RED : GOLD2;                         // confirmed → crimson, AI-only → gold
        const p = P(ZONE_AT[z]), pulse = 0.5 + 0.5 * Math.sin(t * 3 + i);
        const r = (mode === "mini" ? 4 : 6) + Math.min(10, n) * 0.6;
        g.fillStyle = rgba(C, 0.18 + 0.2 * pulse); g.beginPath(); g.arc(p[0], p[1], r * (1.6 + pulse * 0.8), 0, 7); g.fill();
        g.fillStyle = rgba(C, 0.95); g.shadowColor = rgba(C, 1); g.shadowBlur = 14; g.beginPath(); g.arc(p[0], p[1], r * 0.55, 0, 7); g.fill(); g.shadowBlur = 0;
        if (mode === "map") {
          g.font = "600 11px Inter, system-ui, sans-serif"; g.fillStyle = rgba(GOLD2, 0.95);
          g.fillText(`${n}`, p[0] + r + 4, p[1] - r - 2);
        }
      });
    };
    lib.fxRunner(canvas, draw, mode === "mini" ? 30 : 45, { persistent: true });
  };
})(V.lib);
