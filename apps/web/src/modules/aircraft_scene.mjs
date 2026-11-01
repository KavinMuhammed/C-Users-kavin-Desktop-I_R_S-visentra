/* ==========================================================================
   modules/aircraft_scene.mjs — VisionTra 3D aircraft (Three.js r160, MIT).
   Lazy-loaded ES module (dist/vendor/), resolved through the import map in index.html.

   Reference airframe: NASA's Global Hawk (RQ-4) model from NASA 3D Resources (MODEL below), loaded
   after the first frame on hardware GPUs. Fallback, and the first thing drawn: an original
   fighter-inspired concept built in code (pointed chined fuselage, canopy, swept wings, twin
   canted stabilizers, intakes, exhausts, gear). Silver/platinum surfaces, champagne accents.
   Neither is a certified digital twin of an inspected aircraft.

   Defect markers are placed ONLY on components recorded for an inspection (data.zones). The
   marker shows the component, never a point on it: VisionTra does not map image pixels to the
   airframe. Colours: ivory = engineer-confirmed, violet = AI proposals, gold = focused component.

   export mount(container, { mode, zones, focus, reducedMotion, autorotate, model, onPause, onDegrade, onFirstFrame, onModel }) → controller
   Quality tiers: 2 full (IBL, shadows) · 1 no shadows, 1x pixels · 0 software GL (lite materials, render on demand).
   ========================================================================== */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { createNexusField, createStarfield } from "./nexus_field.mjs";

const GOLD = 0xf2d399, GOLD_DEEP = 0xc9a24e, CRIMSON = 0xff2e4d, CYAN = 0x45d9ff;
const R = 1.48;                 // fuselage radius
const X_NOSE = 18.8, X_TAIL = -18.8, NOSE_START = 11.8, TAIL_START = -9.8;
const GROUND_Y = -2.1;

/* ------------------------------------------------------------------ fuselage */
function section(x) {          // → { r, cy } radius and centre height of the fuselage at station x
  if (x > NOSE_START) {
    const u = Math.min(1, (x - NOSE_START) / (X_NOSE - NOSE_START));
    return { r: R * Math.pow(Math.max(0, 1 - Math.pow(u, 2.15)), 0.5), cy: -0.32 * R * u * u };
  }
  if (x < TAIL_START) {
    const u = Math.min(1, (TAIL_START - x) / (TAIL_START - X_TAIL));
    return { r: R * (1 - 0.86 * Math.pow(u, 1.25)), cy: 0.66 * R * Math.pow(u, 1.55) };
  }
  return { r: R, cy: 0 };
}
const surf = (x, th, out = 0) => {   // point on the fuselage skin; th = angle from the crown (0 = top, π/2 = starboard side)
  const s = section(x);
  return new THREE.Vector3(x, s.cy + Math.cos(th) * (s.r * 1.04 + out), Math.sin(th) * (s.r + out));
};
function stations() {
  const xs = [];
  for (let i = 0; i <= 26; i++) xs.push(X_NOSE - (X_NOSE - NOSE_START) * Math.pow(i / 26, 1.6));          // dense at the nose
  for (let x = NOSE_START - 1; x > TAIL_START; x -= 1) xs.push(x);
  for (let i = 0; i <= 24; i++) xs.push(TAIL_START - (TAIL_START - X_TAIL) * (i / 24));
  return xs;
}
// livery (linear RGB, multiplied with the body material): satin titanium crown and sides, graphite belly, a thin
// champagne-gold cheatline between them, and a slightly darker composite radome
const LIV = { crown: [0.78, 0.8, 0.83], belly: [0.085, 0.09, 0.105], gold: [0.86, 0.66, 0.34], radome: [0.52, 0.54, 0.57] };
function liveryAt(x, th) {
  const c = Math.cos(th);                                 // 1 at the crown, −1 at the keel
  if (x > X_NOSE - 1.35) return LIV.radome;
  if (Math.abs(c + 0.2) < 0.05 && x < NOSE_START + 1.6 && x > X_TAIL + 4) return LIV.gold;
  if (c < -0.2) return LIV.belly;
  return LIV.crown;
}
function fuselageGeometry() {
  const xs = stations(), N = 96, pos = [], idx = [], col = [];
  xs.forEach((x) => { for (let k = 0; k <= N; k++) { const th = (k / N) * Math.PI * 2, p = surf(x, th); pos.push(p.x, p.y, p.z); col.push(...liveryAt(x, th)); } });
  for (let i = 0; i < xs.length - 1; i++) for (let k = 0; k < N; k++) {
    const a = i * (N + 1) + k, b = a + N + 1;
    idx.push(a, b, a + 1, a + 1, b, b + 1);
  }
  // tail cap
  const last = xs.length - 1, s = section(X_TAIL), c = pos.length / 3; pos.push(X_TAIL - 0.12, s.cy, 0); col.push(...LIV.belly);
  for (let k = 0; k < N; k++) idx.push(last * (N + 1) + k, c, last * (N + 1) + k + 1);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.setIndex(idx); g.computeVertexNormals();
  return g;
}
function skinPatch(x0, x1, th0, th1, out, nx = 10, nt = 24) {   // a curved patch that follows the skin (glazing)
  const pos = [], idx = [];
  for (let i = 0; i <= nx; i++) for (let k = 0; k <= nt; k++) { const p = surf(x0 + (x1 - x0) * (i / nx), th0 + (th1 - th0) * (k / nt), out); pos.push(p.x, p.y, p.z); }
  for (let i = 0; i < nx; i++) for (let k = 0; k < nt; k++) { const a = i * (nt + 1) + k, b = a + nt + 1; idx.push(a, a + 1, b, a + 1, b + 1, b); }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals(); return g;
}
const skinLine = (pts) => new THREE.BufferGeometry().setFromPoints(pts);
function ring(x, out = 0.012, th0 = 0, th1 = Math.PI * 2, n = 72) { const p = []; for (let k = 0; k <= n; k++) p.push(surf(x, th0 + (th1 - th0) * (k / n), out)); return p; }
function rectOnSkin(xc, thc, w, h, out = 0.014) {   // door outline: width along x, height as arc length
  const s = section(xc), dth = h / (s.r * 1.02) / 2, p = [];
  const steps = 10;
  for (let i = 0; i <= steps; i++) p.push(surf(xc - w / 2 + (w * i) / steps, thc - dth, out));
  for (let i = 0; i <= steps; i++) p.push(surf(xc + w / 2, thc - dth + (2 * dth * i) / steps, out));
  for (let i = 0; i <= steps; i++) p.push(surf(xc + w / 2 - (w * i) / steps, thc + dth, out));
  for (let i = 0; i <= steps; i++) p.push(surf(xc - w / 2, thc + dth - (2 * dth * i) / steps, out));
  return p;
}

/* ------------------------------------------------------------------ lifting surfaces */
function airfoil(n, tc, camber = 0.02) {          // NACA-style section: [s along chord 0..1, h normal to chord], LE → upper → TE → lower → LE
  const up = [], lo = [];
  for (let i = 0; i <= n; i++) {
    const s = (1 - Math.cos((i / n) * Math.PI)) / 2;
    const t = 5 * tc * (0.2969 * Math.sqrt(s) - 0.126 * s - 0.3516 * s * s + 0.2843 * s ** 3 - 0.1036 * s ** 4);
    const p = 0.4, yc = s < p ? (camber / (p * p)) * (2 * p * s - s * s) : (camber / ((1 - p) ** 2)) * (1 - 2 * p + 2 * p * s - s * s);
    up.push([s, yc + t]); lo.push([s, yc - t]);
  }
  return up.concat(lo.reverse().slice(1, -1));
}
/** Loft through sections { le: Vector3, chord, tc, chordDir, thickDir, camber }. Closes the outer end. */
function loft(sections, n = 22) {
  const pos = [], idx = [];
  const prof = sections.map((S) => airfoil(n, S.tc, S.camber ?? 0.02));
  const M = prof[0].length;
  sections.forEach((S, j) => prof[j].forEach(([s, h]) => {
    const p = S.le.clone().addScaledVector(S.chordDir, s * S.chord).addScaledVector(S.thickDir, h * S.chord);
    pos.push(p.x, p.y, p.z);
  }));
  for (let j = 0; j < sections.length - 1; j++) for (let k = 0; k < M; k++) {
    const a = j * M + k, b = j * M + ((k + 1) % M), c = (j + 1) * M + k, d = (j + 1) * M + ((k + 1) % M);
    idx.push(a, c, b, b, c, d);
  }
  const lastS = sections.length - 1, center = new THREE.Vector3();
  for (let k = 0; k < M; k++) center.add(new THREE.Vector3(pos[(lastS * M + k) * 3], pos[(lastS * M + k) * 3 + 1], pos[(lastS * M + k) * 3 + 2]));
  center.divideScalar(M); const ci = pos.length / 3; pos.push(center.x, center.y, center.z);
  for (let k = 0; k < M; k++) idx.push(lastS * M + k, lastS * M + ((k + 1) % M), ci);
  // A mirrored span changes handedness. Orient the upper skin towards thickDir
  // instead of assuming one winding for both wings and the vertical surfaces.
  // Inward normals otherwise hide one wing through normal front-face culling.
  const span = sections[1].le.clone().sub(sections[0].le);
  if (span.cross(sections[0].chordDir).dot(sections[0].thickDir) < 0) {
    for (let i = 0; i < idx.length; i += 3) [idx[i + 1], idx[i + 2]] = [idx[i + 2], idx[i + 1]];
  }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
  return g;
}
const AFT = new THREE.Vector3(-1, 0, 0), UP = new THREE.Vector3(0, 1, 0), SIDE = new THREE.Vector3(0, 0, 1);
const mirrorZ = (g) => { const m = g.clone(); m.scale(1, 1, -1); const ix = m.getIndex().array; for (let i = 0; i < ix.length; i += 3) { const t = ix[i + 1]; ix[i + 1] = ix[i + 2]; ix[i + 2] = t; } m.computeVertexNormals(); return m; };

function wingSections(side) {   // starboard (+z); mirrored for port
  const v = (x, y, z) => new THREE.Vector3(x, y, z * side);
  return [
    { le: v(3.5, -1.15, 1.2), chord: 6.8, tc: 0.15, chordDir: AFT, thickDir: UP },
    { le: v(1.25, -0.82, 6.3), chord: 3.95, tc: 0.13, chordDir: AFT, thickDir: UP },
    { le: v(-4.2, 0.12, 16.6), chord: 1.65, tc: 0.11, chordDir: AFT, thickDir: UP },
    { le: v(-4.55, 0.30, 17.1), chord: 1.42, tc: 0.10, chordDir: AFT, thickDir: UP },
  ];
}
function wingletSections(side) {
  const v = (x, y, z) => new THREE.Vector3(x, y, z * side);
  const thick = new THREE.Vector3(0, 0, side);
  return [
    { le: v(-4.6, 0.25, 17.15), chord: 1.38, tc: 0.08, chordDir: AFT, thickDir: thick, camber: 0 },
    { le: v(-5.3, 1.2, 17.45), chord: 1.0, tc: 0.08, chordDir: AFT, thickDir: thick, camber: 0 },
    { le: v(-6.0, 2.35, 17.6), chord: 0.62, tc: 0.07, chordDir: AFT, thickDir: thick, camber: 0 },
  ];
}
function tailplaneSections(side) {
  const v = (x, y, z) => new THREE.Vector3(x, y, z * side);
  return [
    { le: v(-12.6, 0.95, 0.4), chord: 4.2, tc: 0.11, chordDir: AFT, thickDir: UP, camber: 0 },
    { le: v(-15.9, 1.35, 6.25), chord: 1.55, tc: 0.09, chordDir: AFT, thickDir: UP, camber: 0 },
  ];
}
function finSections() {
  return [
    { le: new THREE.Vector3(-10.4, 1.55, 0), chord: 6.4, tc: 0.12, chordDir: AFT, thickDir: SIDE, camber: 0 },
    { le: new THREE.Vector3(-13.6, 4.8, 0), chord: 3.9, tc: 0.11, chordDir: AFT, thickDir: SIDE, camber: 0 },
    { le: new THREE.Vector3(-16.4, 7.9, 0), chord: 2.25, tc: 0.10, chordDir: AFT, thickDir: SIDE, camber: 0 },
  ];
}
const leLine = (secs) => secs.map((S) => S.le.clone().addScaledVector(S.thickDir, 0.012 * S.chord));
const teLine = (secs) => secs.map((S) => S.le.clone().addScaledVector(S.chordDir, S.chord));

/* ------------------------------------------------------------------ engines and gear */
function lathe(profile, segs = 48) {    // profile [[x, r]] around the x axis
  const pts = profile.map(([x, r]) => new THREE.Vector2(r, x));
  const g = new THREE.LatheGeometry(pts, segs);
  g.rotateZ(-Math.PI / 2);   // lathe axis y → x
  return g;
}
function engine(mats, side) {
  const grp = new THREE.Group();
  const L = 4.4;
  const nac = lathe([[0.02, 0.83], [0, 0.9], [-0.08, 0.98], [-0.3, 1.04], [-1.4, 1.06], [-2.6, 1.0], [-3.5, 0.82], [-L, 0.64]].map(([x, r]) => [x, r]), 56);
  grp.add(new THREE.Mesh(nac, mats.nacelle));
  const inner = lathe([[0.02, 0.83], [-0.35, 0.8], [-L + 0.05, 0.6]], 56); inner.scale(1, 1, 1);
  const innerMesh = new THREE.Mesh(inner, mats.inlet); innerMesh.material.side = THREE.BackSide; grp.add(innerMesh);
  const lip = new THREE.Mesh(new THREE.TorusGeometry(0.86, 0.045, 12, 64), mats.gold); lip.rotation.y = Math.PI / 2; lip.position.x = 0.0; grp.add(lip);
  const fan = new THREE.Mesh(new THREE.CircleGeometry(0.8, 48), mats.fan); fan.rotation.y = Math.PI / 2; fan.position.x = -0.38; grp.add(fan);
  for (let k = 0; k < 22; k++) {     // fan blades, dark, barely visible behind the lip
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.62, 0.11), mats.blade);
    b.position.set(-0.36, Math.cos((k / 22) * Math.PI * 2) * 0.42, Math.sin((k / 22) * Math.PI * 2) * 0.42);
    b.rotation.x = (k / 22) * Math.PI * 2; b.rotation.y = 0.5; grp.add(b);
  }
  const spinner = lathe([[-0.05, 0.0], [-0.12, 0.12], [-0.3, 0.22], [-0.42, 0.24]], 32); grp.add(new THREE.Mesh(spinner, mats.spinner));
  const core = lathe([[-L + 0.1, 0.5], [-L - 0.35, 0.42], [-L - 0.95, 0.2], [-L - 1.25, 0.02]], 40); grp.add(new THREE.Mesh(core, mats.core));
  const nozzle = new THREE.Mesh(new THREE.TorusGeometry(0.64, 0.03, 10, 56), mats.gunmetal); nozzle.rotation.y = Math.PI / 2; nozzle.position.x = -L; grp.add(nozzle);
  // pylon: a thin swept fairing from the nacelle crown into the wing underside
  const py = loft([
    { le: new THREE.Vector3(-0.7, 0.98, 0), chord: 3.4, tc: 0.08, chordDir: AFT, thickDir: SIDE, camber: 0 },
    { le: new THREE.Vector3(-0.2, 1.42, 0), chord: 3.2, tc: 0.08, chordDir: AFT, thickDir: SIDE, camber: 0 },
  ], 12);
  grp.add(new THREE.Mesh(py, mats.wing));
  grp.position.set(4.05, -2.05, 5.75 * side);
  return grp;
}
function wheel(mats, r, w) {
  const g = new THREE.Group();
  const tyre = new THREE.Mesh(new THREE.TorusGeometry(r - w * 0.35, w * 0.48, 16, 40), mats.tyre); g.add(tyre);
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.55, r * 0.55, w * 0.8, 32), mats.hub); hub.rotation.x = Math.PI / 2; g.add(hub);
  return g;
}
function gear(mats) {
  const grp = new THREE.Group();
  const strut = (x, z, top, bottom, rad) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(rad, rad, top - bottom, 16), mats.strut); m.position.set(x, (top + bottom) / 2, z); return m; };
  for (const side of [1, -1]) {              // main gear under the wing roots
    const z = 3.85 * side;
    grp.add(strut(-1.3, z, -1.0, -3.3, 0.13));
    grp.add(strut(-1.3, z, -2.0, -3.15, 0.07).rotateZ(0.0));
    const axle = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 1.05, 12), mats.strut); axle.rotation.x = Math.PI / 2; axle.position.set(-1.3, -3.35, z); grp.add(axle);
    for (const dz of [-0.45, 0.45]) { const w = wheel(mats, 0.58, 0.36); w.position.set(-1.3, GROUND_Y + 0.58, z + dz); grp.add(w); }
    const bay = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.05, 1.2), mats.bay); bay.position.set(-1.2, -1.42, 2.6 * side); grp.add(bay);
  }
  grp.add(strut(13.9, 0, -1.7, -3.45, 0.09));
  const naxle = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.7, 12), mats.strut); naxle.rotation.x = Math.PI / 2; naxle.position.set(13.9, GROUND_Y + 0.38, 0); grp.add(naxle);
  for (const dz of [-0.26, 0.26]) { const w = wheel(mats, 0.38, 0.24); w.position.set(13.9, GROUND_Y + 0.38, dz); grp.add(w); }
  return grp;
}

/* ------------------------------------------------------------------ the aircraft */
export const ZONES = { // Component anchors on the fighter concept, never image-localized defects.
  fuselage: [2, .8, 0], wing_upper: [-2,.15,-4], wing_lower: [-2,-.2,4], empennage: [-6.2,2,1.8],
  nacelle: [-7.7,0,-.75], control_surface: [-5,.1,-5], landing_gear: [-2.3,-1.6,1.25],
};
/* NASA Global Hawk (RQ-4) — NASA 3D Resources, converted once offline by scripts/assets/convert_global_hawk.py: Draco
   compression removed and every texture, image and UV removed (the source textures carry the NASA insignia, which are
   protected marks). Model frame: +Y up, nose +Z, fuselage length 1, resting on y = 0. NASA media are not subject to US
   copyright; NASA is credited and no NASA endorsement is implied. */
export const MODEL = { id: "global_hawk", url: "/vendor/models/global_hawk.glb", bytes: 848792, length: 14.5,
  sha256: "b968f1285d86e6b219f35642a4e5e122ed63835b1e3cf7fbf804f76fa704da2e",
  credit: "Global Hawk 3D model: NASA (NASA 3D Resources), textures and insignia removed. No NASA endorsement implied.",
  creditShort: "3D model: NASA · no endorsement implied" };
// Component anchors on the Global Hawk, in its model frame, measured on the converted mesh (crown, top-mounted engine,
// V-tail, mid-span wing skin, outboard trailing edge, main gear), each snapped to its nearest mesh vertex. Like ZONES they
// mark a component, never a defect position.
export const ZONES_GLOBAL_HAWK = {
  fuselage: [0, .192, .147], wing_upper: [.483, .112, -.045], wing_lower: [-.483, .102, -.045], empennage: [.147, .26, -.437],
  nacelle: [0, .266, -.228], control_surface: [-.713, .105, -.094], landing_gear: [.148, .03, -.021],
};
export const hawkToScene = ([x, y, z]) => [MODEL.length * z, MODEL.length * y + GROUND_Y, -MODEL.length * x];   // = rotation.y +90°, scale, lift

/** Minimal reader for the converted asset only: glTF 2.0 binary, triangles, float32 POSITION/NORMAL, uint16/uint32
    indices, base colours. Anything else (extensions, textures, images, URIs, out-of-range indices) is rejected. */
export function parseGLB(buf) {
  const dv = new DataView(buf), fail = (m) => { throw new Error("aircraft model: " + m); };
  if (buf.byteLength < 28 || dv.getUint32(0, true) !== 0x46546c67 || dv.getUint32(4, true) !== 2 || dv.getUint32(8, true) !== buf.byteLength) fail("not a glTF 2.0 binary");
  const jsonLen = dv.getUint32(12, true);
  if (dv.getUint32(16, true) !== 0x4e4f534a) fail("missing JSON chunk");
  const g = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 20, jsonLen)));
  const binAt = 20 + jsonLen;
  if (dv.getUint32(binAt + 4, true) !== 0x004e4942) fail("missing BIN chunk");
  const bin = binAt + 8, binLen = dv.getUint32(binAt, true);
  if (g.extensionsUsed || g.extensionsRequired || g.images || g.textures || (g.buffers || []).some((b) => b.uri)) fail("unexpected extensions, textures or external data");
  const read = (i, kind) => {
    const a = g.accessors[i], v = g.bufferViews[a.bufferView], n = { SCALAR: 1, VEC3: 3 }[a.type];
    const T = { 5126: Float32Array, 5123: Uint16Array, 5125: Uint32Array }[a.componentType];
    if (!T || !n || (kind === "f" ? T !== Float32Array : T === Float32Array)) fail("unsupported accessor");
    const off = (v.byteOffset || 0) + (a.byteOffset || 0);
    if (off + a.count * n * T.BYTES_PER_ELEMENT > binLen || (v.byteStride && v.byteStride !== n * T.BYTES_PER_ELEMENT)) fail("accessor out of range");
    return new T(buf.slice(bin + off, bin + off + a.count * n * T.BYTES_PER_ELEMENT));
  };
  const parts = g.meshes[0].primitives.map((p) => {
    if ((p.mode ?? 4) !== 4 || p.extensions) fail("unsupported primitive");
    const position = read(p.attributes.POSITION, "f"), normal = p.attributes.NORMAL != null ? read(p.attributes.NORMAL, "f") : null, index = read(p.indices, "i");
    const nv = position.length / 3;
    for (let k = 0; k < index.length; k++) if (index[k] >= nv) fail("index out of range");
    for (let k = 0; k < position.length; k++) if (!Number.isFinite(position[k])) fail("non-finite vertex");
    const m = g.materials[p.material] || {};
    return { name: m.name || "", base: (m.pbrMetallicRoughness || {}).baseColorFactor || null, position, normal, index };
  });
  return { parts, asset: g.asset || {}, extras: g.extras || {} };
}

// source material → VisionTra surface (no source colours are used: the airframe follows the app's palette)
const HAWK_SURFACE = [[/lambert4SG/, "tyre"], [/justwheels_a/, "hub"], [/blinn1SG/, "gunmetal"], [/initialShadingGroup/, "strut"]];
function buildHawk(model, mats) {
  const ac = new THREE.Group(); ac.name = "global-hawk";
  for (const p of model.parts) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(p.position, 3));
    if (p.normal) geo.setAttribute("normal", new THREE.BufferAttribute(p.normal, 3)); else geo.computeVertexNormals();
    geo.setIndex(new THREE.BufferAttribute(p.index, 1));
    const key = (HAWK_SURFACE.find(([re]) => re.test(p.name)) || [, "hawkSkin"])[1];
    const o = new THREE.Mesh(geo, mats[key]); o.name = "hawk-" + key; o.castShadow = o.receiveShadow = true; ac.add(o);
    if (key === "hawkSkin" && mats.edge) { const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 32), mats.edge); e.name = "hawk-edges"; e.renderOrder = 2; ac.add(e); }
  }
  ac.rotation.y = Math.PI / 2; ac.scale.setScalar(MODEL.length); ac.position.y = GROUND_Y;
  return ac;
}

export const ZONE_LABEL = { fuselage: "Fuselage", wing_upper: "Wing — upper", wing_lower: "Wing — lower", empennage: "Empennage / tail", nacelle: "Engine nacelle", control_surface: "Control surface", landing_gear: "Landing gear" };

function materials(env, lite) {
  // lite (software rasteriser): one MeshStandard program instead of several physical variants —
  // shader compilation there can otherwise block the page for seconds
  const m = lite
    ? ({ clearcoat, clearcoatRoughness, sheen, sheenRoughness, sheenColor, metalness = 0, roughness = 0.5, ...o }) => new THREE.MeshStandardMaterial({ ...o, metalness: metalness * 0.35, roughness: Math.min(1, roughness + 0.12) })
    : (o) => new THREE.MeshPhysicalMaterial({ envMapIntensity: 1.0, ...o });
  return {
    body: m({ color: 0xffffff, vertexColors: true, metalness: 0.35, roughness: 0.46, clearcoat: 0.45, clearcoatRoughness: 0.22 }),
    belly: m({ color: 0x2a2c31, metalness: 0.7, roughness: 0.42, clearcoat: 0.3 }),
    fin: m({ color: 0x33363c, metalness: 0.8, roughness: 0.34, clearcoat: 0.5, clearcoatRoughness: 0.2 }),
    antenna: new THREE.MeshStandardMaterial({ color: 0x1c1e22, metalness: 0.4, roughness: 0.6 }),
    wing: m({ color: 0x9da3ab, metalness: 0.72, roughness: 0.4, clearcoat: 0.2 }),
    // Airframe finish (Phase 1 UI, 29 Oct): painted skin is a dielectric, so it is modelled as satin titanium-grey paint
    // under a clearcoat. The earlier metalness 0.8 made the whole airframe read as one chrome/plastic object and blew
    // out the wing highlight.
    hawkSkin: m({ color: 0x474c55, metalness: 0.22, roughness: 0.4, clearcoat: 0.85, clearcoatRoughness: 0.14, envMapIntensity: 1.0, sheen: 0.2, sheenRoughness: 0.55, sheenColor: 0xd9cdb4 }),
    // feature edges of the real mesh (creases ≥ 32°: control-surface gaps, intake lip, tail roots), never invented panel lines
    edge: new THREE.LineBasicMaterial({ color: 0xe9d6ac, transparent: true, opacity: 0.3, depthWrite: false }),
    nacelle: m({ color: 0x41454c, metalness: 0.85, roughness: 0.28, clearcoat: 0.5, clearcoatRoughness: 0.15 }),
    gunmetal: m({ color: 0x2c2f34, metalness: 0.9, roughness: 0.35 }),
    inlet: new THREE.MeshStandardMaterial({ color: 0x0b0c0e, metalness: 0.4, roughness: 0.8 }),
    fan: new THREE.MeshStandardMaterial({ color: 0x050506, metalness: 0.2, roughness: 0.95 }),
    blade: new THREE.MeshStandardMaterial({ color: 0x24262a, metalness: 0.9, roughness: 0.4 }),
    spinner: m({ color: 0x1d1f22, metalness: 0.9, roughness: 0.25 }),
    core: m({ color: 0x33363b, metalness: 0.85, roughness: 0.45 }),
    gold: m({ color: GOLD_DEEP, metalness: 0.92, roughness: 0.3 }),
    glass: m({ color: 0x07090d, metalness: 0.3, roughness: 0.08, clearcoat: 1, clearcoatRoughness: 0.05 }),
    window: new THREE.MeshStandardMaterial({ color: 0x0a0d12, metalness: 0.3, roughness: 0.2, emissive: 0xffd28a, emissiveIntensity: 0.05 }),
    strut: m({ color: 0xb8bec6, metalness: 0.95, roughness: 0.25 }),
    tyre: new THREE.MeshStandardMaterial({ color: 0x101113, metalness: 0.0, roughness: 0.85 }),
    hub: m({ color: 0x8d939b, metalness: 0.9, roughness: 0.3 }),
    bay: new THREE.MeshStandardMaterial({ color: 0x15171a, roughness: 0.9 }),
    seam: new THREE.LineBasicMaterial({ color: 0x4a5058, transparent: true, opacity: 0.16 }),
    door: new THREE.LineBasicMaterial({ color: 0x1b1e22, transparent: true, opacity: 0.55 }),
    contour: new THREE.LineBasicMaterial({ color: GOLD, transparent: true, opacity: 0.45 }),
  };
}

// Original fighter-inspired concept, ~20 m long / 13.4 m span. No manufacturer
// identity, weapons, certified geometry or flight-performance claim.
function fighterBody() {
  const rings=[[10.2,.015,.015,-.12],[9,.28,.24,-.06],[7,.65,.48,0],[5,.88,.57,0],
    [3,1.16,.64,0],[1,1.68,.68,-.05],[-2,1.78,.64,-.08],[-5,1.58,.58,-.05],[-7.8,1.45,.5,0],[-9,1.38,.38,0]];
  const pos=[],idx=[],n=64;
  for(const [x,w,h,cy] of rings)for(let j=0;j<=n;j++){
    const t=j/n*Math.PI*2;pos.push(x,cy+Math.cos(t)*h,Math.sin(t)*w);
  }
  for(let i=0;i<rings.length-1;i++)for(let j=0;j<n;j++){
    const a=i*(n+1)+j,b=a+n+1;idx.push(a,b,a+1,a+1,b,b+1);
  }
  const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.Float32BufferAttribute(pos,3));g.setIndex(idx);g.computeVertexNormals();return g;
}
function fighterSurfaces(side) {
  const v=(x,y,z)=>new THREE.Vector3(x,y,z*side);
  const sec=(x,y,z,chord,thickDir=UP)=>({le:v(x,y,z),chord,tc:.065,camber:0,chordDir:AFT,thickDir});
  return {
    wing:[sec(3.2,.03,.75,8.6),sec(.7,.02,2.5,6.5),sec(-3.6,.15,6.7,2.15)],
    tail:[sec(-5.2,.15,1.05,3.6),sec(-7.3,.15,3.9,1.5)],
    fin:[sec(-4.1,.5,1.05,4.2,SIDE),sec(-5.8,3.2,2.25,1.8,SIDE)]
  };
}
function buildAircraft(mats) {
  const ac=new THREE.Group();
  const silver=new THREE.MeshPhysicalMaterial({color:0xaab1b8,metalness:.65,roughness:.42,clearcoat:.2});
  const platinum=new THREE.MeshStandardMaterial({color:0xe1e0db,metalness:.75,roughness:.3});
  const canopyMat=new THREE.MeshPhysicalMaterial({color:0x29251e,metalness:.6,roughness:.16,clearcoat:1});
  const add=(g,m,name)=>{const o=new THREE.Mesh(g,m);o.name=name;o.castShadow=o.receiveShadow=true;ac.add(o);return o;};
  add(fighterBody(),silver,"fighter-fuselage");
  const canopy=add(new THREE.SphereGeometry(1,48,24),canopyMat,"canopy");canopy.scale.set(2.25,.83,.65);canopy.position.set(4.35,.58,0);
  const rim=[];for(let i=0;i<=64;i++){const t=i/64*Math.PI*2;rim.push(new THREE.Vector3(4.35+2.22*Math.cos(t),.69,.66*Math.sin(t)));}
  ac.add(new THREE.Line(skinLine(rim),mats.contour));
  // Angled inlet tunnels: visible recessed mouths, thick lips and a tapered duct.
  for(const side of [-1,1]) {
    const S=fighterSurfaces(side);
    add(loft(S.wing,28),silver,side>0?"wing_starboard":"wing_port");
    add(loft(S.tail,22),silver,"tailplane");
    add(loft(S.fin,24),silver,"canted-fin");
    const pts=S.wing.map(q=>q.le.clone().addScaledVector(AFT,q.chord*.77).add(new THREE.Vector3(0,.035,0)));
    ac.add(new THREE.Line(skinLine(pts),mats.door));
    const inlet=new THREE.Group();
    const shell=new THREE.Mesh(new THREE.BoxGeometry(3.7,.94,1.0),silver);shell.position.set(-.6,-.37,side*1.1);inlet.add(shell);
    const mouth=new THREE.Mesh(new THREE.PlaneGeometry(.83,.70),mats.inlet);mouth.rotation.y=Math.PI/2;mouth.position.set(1.27,-.37,side*1.1);inlet.add(mouth);
    // Four separate lip rails leave the black throat visible from the front.
    for(const dy of [-.43,.43]){const lip=new THREE.Mesh(new THREE.BoxGeometry(.12,.08,1.02),platinum);lip.position.set(1.31,-.37+dy,side*1.1);inlet.add(lip);}
    for(const dz of [-.48,.48]){const lip=new THREE.Mesh(new THREE.BoxGeometry(.12,.86,.07),platinum);lip.position.set(1.31,-.37,side*1.1+dz);inlet.add(lip);}
    ac.add(inlet);
    const nozzle=add(new THREE.CylinderGeometry(.57,.69,1.35,32,1,true),mats.gunmetal,"exhaust-nozzle");nozzle.rotation.z=Math.PI/2;nozzle.position.set(-8.95,-.03,side*.75);
    const dark=add(new THREE.CircleGeometry(.53,32),mats.inlet,"exhaust-throat");dark.rotation.y=-Math.PI/2;dark.position.set(-9.45,-.03,side*.75);
    const ring=add(new THREE.TorusGeometry(.58,.045,8,40),platinum,"exhaust-rim");ring.rotation.y=Math.PI/2;ring.position.set(-9.62,-.03,side*.75);
    for(let i=0;i<16;i++){const t=i/16*Math.PI*2;
      const petal=add(new THREE.BoxGeometry(.85,.025,.06),mats.gunmetal,"nozzle-petal");petal.position.set(-9.15,-.03+.62*Math.cos(t),side*.75+.62*Math.sin(t));petal.rotation.x=t;}
    const hinge=S.fin.map(q=>q.le.clone().addScaledVector(AFT,q.chord*.78).add(new THREE.Vector3(0,0,side*.04)));
    ac.add(new THREE.Line(skinLine(hinge),mats.door));
    // Champagne fin leading-edge identity accent, not a defect marker.
    ac.add(new THREE.Line(skinLine(S.fin.map(q=>q.le)),mats.contour));
  }
  for(const [x,z,top] of [[5.5,0,-.4],[-2.3,-1.25,-.55],[-2.3,1.25,-.55]]){
    const leg=add(new THREE.CylinderGeometry(.055,.075,1.25,12),mats.strut,"landing-strut");leg.position.set(x,top-.625,z);
    const tyre=wheel(mats,.28,.18);tyre.position.set(x,GROUND_Y+.28,z);ac.add(tyre);
    const door=add(new THREE.BoxGeometry(.8,.04,.4),silver,"gear-door");door.position.set(x,top,z+.2);
  }
  return ac;
}

/* ------------------------------------------------------------------ stage: ground, lights, scan */
function groundTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 1024; const g = c.getContext("2d");
  const rg = g.createRadialGradient(512, 512, 40, 512, 512, 512); rg.addColorStop(0, "rgba(30,26,22,0.95)"); rg.addColorStop(0.55, "rgba(14,12,12,0.7)"); rg.addColorStop(1, "rgba(5,5,7,0)");
  g.fillStyle = rg; g.fillRect(0, 0, 1024, 1024);
  g.save(); g.globalCompositeOperation = "source-atop";
  for (let i = 0; i <= 0; i++) {
    const p = (i / 32) * 1024, major = i % 4 === 0;
    g.strokeStyle = major ? "rgba(242,211,153,0.30)" : "rgba(242,211,153,0.10)"; g.lineWidth = major ? 1.6 : 1;
    g.beginPath(); g.moveTo(p, 0); g.lineTo(p, 1024); g.stroke(); g.beginPath(); g.moveTo(0, p); g.lineTo(1024, p); g.stroke();
  }
  g.strokeStyle = "rgba(210,205,190,0.14)"; g.lineWidth = 2; g.beginPath(); g.arc(512, 512, 300, 0, Math.PI * 2); g.stroke();
  g.restore();
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t;
}

const PRESETS = {
  three_quarter: { pos: [26, 15, 28], target: [0, -0.6, 0] },
  front: { pos: [44, 1.2, 0.001], target: [0, -0.6, 0] },
  side: { pos: [0.001, 2.2, 46], target: [0, -0.6, 0] },
  top: { pos: [0.001, 58, 0.002], target: [0, -0.6, 0], k: 1.32 },
  rear: { pos: [-38, 7, -18], target: [0, 0.5, 0] },
};

export function webglAvailable() {
  try { const c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl"))); } catch { return false; }
}

function softwareRenderer() {
  try {
    const gl = document.createElement("canvas").getContext("webgl"); if (!gl) return true;
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const name = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    const lose = gl.getExtension("WEBGL_lose_context"); lose && lose.loseContext();
    return /swiftshader|llvmpipe|softpipe|basic render|microsoft basic|software/i.test(name);
  } catch { return false; }
}

export function mount(container, opts = {}) {
  const mode = opts.mode || "map";
  const reduced = !!opts.reducedMotion;
  // quality tier: software rasterisers (SwiftShader, llvmpipe, WARP) get a light profile from the start
  const forceFull = /[?&]gl=full\b/.test(location.search);   // QA only: full quality even on a software rasteriser
  // the caller's cached probe is used when given: creating a WebGL context just to read the GPU name cost ~0.7 s per mount
  const soft = !forceFull && (opts.soft != null ? !!opts.soft : softwareRenderer());
  let tier = soft ? 0 : 2;                                  // 2 full · 1 no shadows, 1x pixels · 0 + render on demand
  // opts.canvas/context: the caller's GPU probe context, adopted so the page creates one WebGL context instead of two
  const adopt = !soft && opts.canvas && opts.context ? { canvas: opts.canvas, context: opts.context } : {};
  const renderer = new THREE.WebGLRenderer({ antialias: !soft, alpha: true, powerPreference: "high-performance", ...adopt });
  const dprCap = () => (tier >= 2 ? Math.min(window.devicePixelRatio || 1, mode === "focus" ? 1.5 : 1.75) : 1);
  renderer.setPixelRatio(dprCap());
  // Obsidian Nexus: slightly lower exposure so the airframe reads as metal with form, not a white silhouette
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 0.92;
  renderer.shadowMap.enabled = tier >= 2; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.className = "ac3d-gl"; renderer.domElement.setAttribute("aria-hidden", "true");
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x050407, 70, 150);
  // image-based lighting from a prefiltered studio room; skipped on software rasterisers (the
  // prefilter passes alone take seconds there) where the lite materials rely on direct lights
  let envTex = null;
  if (tier > 0) { const pmrem = new THREE.PMREMGenerator(renderer); envTex = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture; scene.environment = envTex; pmrem.dispose(); }
  const mats = materials(envTex, tier === 0);
  // the fighter concept is drawn first; the NASA Global Hawk replaces it once loaded and verified (loadHawk below)
  let aircraft = buildAircraft(mats); scene.add(aircraft);
  let zoneTable = ZONES, modelId = "fighter";

  // Decoration only: evidence focus views and software renderers remain quiet.
  const field = tier > 0 && mode !== "focus" ? createNexusField(THREE, {
    count: mode === "hero" ? 2800 : 1100, reduced, entrance: !!opts.entrance
  }) : null;
  if (field) scene.add(field.points);
  // distant stars only in hero scenes (intro, product and sign-in pages), never behind evidence
  const stars = tier > 0 && mode === "hero" ? createStarfield(THREE, { count: 700 }) : null;
  if (stars) scene.add(stars.points);
  // intro Act III: the airframe is revealed by the camera dolly once the reference model is ready (or after 3.2 s with
  // the fighter fallback), instead of showing the fighter first and swapping it mid-sequence
  let revealTimer = 0;
  if (opts.entrance && !reduced) {
    aircraft.visible = false;
    revealTimer = setTimeout(() => { if (aircraft) aircraft.visible = true; requestRender(); }, 3200);
  }

  // studio lights: warm key, gold edge rim, violet counter-rim and ambient, cool under-fill
  // Phase 1 UI: neutral sky/ground ambient (the violet one tinted the whole airframe), a cool top rim that separates the
  // silhouette from the dark, and gold/violet kept as restrained accents from behind
  scene.add(new THREE.HemisphereLight(0x9aa2b4, 0x0b0907, tier > 0 ? 0.22 : 1.4));
  const key = new THREE.DirectionalLight(0xfff1dc, 1.7); key.position.set(22, 30, 18); key.castShadow = tier >= 2;
  key.shadow.mapSize.set(2048, 2048); Object.assign(key.shadow.camera, { left: -26, right: 26, top: 26, bottom: -26, near: 5, far: 90 }); key.shadow.bias = -0.0004; key.shadow.normalBias = 0.03; scene.add(key);
  const rimGold = new THREE.DirectionalLight(GOLD, 0.95); rimGold.position.set(-30, 14, -22); scene.add(rimGold);
  const rimCool = new THREE.DirectionalLight(0xcfd9ff, 0.7); rimCool.position.set(-6, 26, -30); scene.add(rimCool);
  const rimViolet = new THREE.DirectionalLight(0x9b7bff, 0.35); rimViolet.position.set(-16, 6, 24); scene.add(rimViolet);
  const fill = new THREE.DirectionalLight(0xbca0ff, 0.18); fill.position.set(10, -6, -25); scene.add(fill);

  const ground = new THREE.Mesh(new THREE.CircleGeometry(48, 96), new THREE.MeshBasicMaterial({ map: groundTexture(), transparent: true, depthWrite: false }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = GROUND_Y - 0.01; scene.add(ground);
  const shadowCatcher = new THREE.Mesh(new THREE.CircleGeometry(40, 64), new THREE.ShadowMaterial({ opacity: 0.55 }));
  shadowCatcher.rotation.x = -Math.PI / 2; shadowCatcher.position.y = GROUND_Y; shadowCatcher.receiveShadow = true; shadowCatcher.visible = tier >= 2; scene.add(shadowCatcher);

  // Decorative lighting only; never an indication of inference or scan progress.
  const sweep = new THREE.Mesh(new THREE.PlaneGeometry(0.35, 22), new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.09, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
  sweep.rotation.y = Math.PI / 2; sweep.position.y = 1; scene.add(sweep); sweep.visible = false;

  // component markers (only for recorded components)
  const markerGroup = new THREE.Group(); scene.add(markerGroup);
  const markers = [];
  let lastZones = opts.zones, lastFocus = opts.focus;
  function setZones(zones, focus) {
    lastZones = zones; lastFocus = focus;
    markerGroup.traverse((o) => { o.geometry && o.geometry.dispose(); o.material && o.material.dispose(); });
    markerGroup.clear(); markers.length = 0;
    const entries = Object.entries(zones || {}).filter(([z]) => zoneTable[z]);
    for (const [z, v] of entries) {
      const count = (n) => Number.isFinite(Number(n)) && Number(n) > 0 ? Number(n) : 0;
      const confirmed = count(v && typeof v === "object" ? v.confirmed : v), ai = count(v && typeof v === "object" ? v.ai : 0);
      if (!confirmed && !ai) continue;
      const col = confirmed ? 0xeae5da : 0xb497ff;
      const g = new THREE.Group(); g.position.fromArray(zoneTable[z]);
      const core = new THREE.Mesh(new THREE.SphereGeometry(0.32, 20, 14), new THREE.MeshBasicMaterial({ color: col, depthTest: false }));
      const halo = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.68, 40), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.75, side: THREE.DoubleSide, depthWrite: false, depthTest: false }));
      core.renderOrder = halo.renderOrder = 10;
      g.add(core, halo); g.scale.setScalar(1.7); markerGroup.add(g); markers.push({ g, halo, zone: z });
    }
    if (focus && zoneTable[focus] && !markers.some((m) => m.zone === focus)) {
      const g = new THREE.Group(); g.position.fromArray(zoneTable[focus]);
      const halo = new THREE.Mesh(new THREE.RingGeometry(0.6, 0.74, 40), new THREE.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false, depthTest: false }));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.22, 16, 10), new THREE.MeshBasicMaterial({ color: GOLD, depthTest: false }));
      halo.renderOrder = dot.renderOrder = 10; g.add(halo, dot); g.scale.setScalar(1.7); markerGroup.add(g); markers.push({ g, halo, zone: focus });
    }
    requestRender();
  }

  const camera = new THREE.PerspectiveCamera(34, 1, 0.5, 400);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08; controls.enablePan = false;
  controls.minDistance = 16; controls.maxDistance = 85; controls.minPolarAngle = 0.05; controls.maxPolarAngle = Math.PI / 2 - 0.04;
  controls.autoRotate = !reduced && opts.autorotate !== false; controls.autoRotateSpeed = mode === "hero" ? 0.55 : 0.4;
  controls.zoomSpeed = 0.7; controls.rotateSpeed = 0.6;
  // frame the aircraft: preset directions are fixed, the distance follows the viewport so the whole
  // airframe fits whatever the panel's aspect ratio (wide hero, square side panel, phone)
  let bound = new THREE.Box3().setFromObject(aircraft).getBoundingSphere(new THREE.Sphere());
  const fitFor = (P) => {
    const v = THREE.MathUtils.degToRad(camera.fov) / 2, h = Math.atan(Math.tan(v) * camera.aspect);
    const k = camera.aspect < 1.1 ? 0.9 : mode === "hero" ? 0.82 : mode === "focus" ? 0.5 : 0.66;
    return (bound.radius / Math.sin(Math.min(v, h))) * k * (P.k || 1);
  };
  let focusV = null, mirror = false;
  // a focused zone on the far (−z) side is viewed from that side so the airframe does not hide it
  const refocus = () => { focusV = opts.focus && zoneTable[opts.focus] ? new THREE.Vector3().fromArray(zoneTable[opts.focus]) : null; mirror = mode === "focus" && !!focusV && focusV.z < -1; };
  refocus();
  const presetPos = (P) => { const v = new THREE.Vector3().fromArray(P.pos); if (mirror) v.z = -v.z; return v.normalize().multiplyScalar(fitFor(P)); };
  let current = PRESETS[opts.preset] ? opts.preset : "three_quarter";
  const p0 = PRESETS[current];
  camera.position.fromArray(p0.pos); controls.target.fromArray(p0.target);
  if (focusV) controls.target.lerp(focusV, 0.45);
  controls.update();

  // camera animation between presets
  let anim = null;
  function goTo(name) {
    const P = PRESETS[name]; if (!P) return; current = name; touched = false; opts.onView && opts.onView(name);
    const from = camera.position.clone(), to = presetPos(P), tf = controls.target.clone(), tt = new THREE.Vector3().fromArray(P.target);
    if (reduced) { camera.position.copy(to); controls.target.copy(tt); controls.update(); requestRender(); return; }
    // move along a sphere so the camera never passes through the aircraft
    const r0 = from.length(), r1 = to.length();
    anim = { t0: performance.now(), dur: 1100, step: (k) => {
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      const dir = from.clone().normalize().lerp(to.clone().normalize(), e).normalize();
      camera.position.copy(dir.multiplyScalar(r0 + (r1 - r0) * e)); controls.target.lerpVectors(tf, tt, e);
    } };
    requestRender();
  }

  // render loop: runs only while visible and something moves
  let raf = 0, visible = true, lastInteract = 0, disposed = false, paused = !controls.autoRotate || tier === 0, refit = false;
  let lastFrame = 0, ema = 0, nFrames = 0, compiled = false, compiling = true, fieldFrame = 0;
  const clock = new THREE.Clock();
  // adaptive governor: if drawing a frame costs the main thread too much, step the quality down
  // (shadows off → 1x pixels → rotation off, render only on interaction) so the app stays responsive
  const WARMUP = 24, minTier = soft ? 0 : 1;          // a hardware GPU never drops to the on-demand tier
  function degrade() {
    if (tier <= minTier) return;
    tier -= 1; nFrames = 0; ema = 0;
    if (tier <= 1) { renderer.shadowMap.enabled = false; key.castShadow = false; shadowCatcher.visible = false; renderer.setPixelRatio(1); resize(); scene.traverse((o) => { if (o.material) [].concat(o.material).forEach((m) => (m.needsUpdate = true)); }); }
    if (tier === 0) { paused = true; sweep.visible = false; opts.onPause && opts.onPause(true); opts.onDegrade && opts.onDegrade("Simplified rendering · drag to turn"); }
  }
  let held = false;                                    // hold(): covered by the intro; draw nothing until released
  function frame(ts) {
    raf = 0; if (disposed || !compiled || held) return;
    const t = clock.getElapsedTime(), now = performance.now();
    // idle auto-rotation runs at ~30 fps; interaction and camera moves run at full rate
    const idle = !anim && now - lastInteract > 1600;
    if (idle && !paused && now - lastFrame < 31) { if (!raf) raf = requestAnimationFrame(frame); return; }
    lastFrame = now;
    if (field) { field.update(fieldFrame ? (now - fieldFrame) / 1000 : 0, !paused && visible && !document.hidden); fieldFrame = now; }
    if (anim) { const k = Math.min(1, (now - anim.t0) / anim.dur); anim.step(k); if (k >= 1) { anim = null; if (refit) { refit = false; resize(); } } }
    controls.autoRotate = !paused && !anim;
    controls.update();
    if (sweep.visible && tier > 0 && !paused) { const u = ((t % 12) / 12); sweep.position.x = 19 - u * 40; sweep.material.opacity = 0.055 * Math.sin(Math.PI * u); }
    if (!reduced && !paused) for (const m of markers) { const s = 1 + 0.06 * Math.sin(t * 1.6); m.halo.scale.setScalar(s); m.halo.lookAt(camera.position); }
    else for (const m of markers) m.halo.lookAt(camera.position);
    const r0 = performance.now(); renderer.render(scene, camera); const dt = performance.now() - r0;
    nFrames += 1; ema = nFrames <= WARMUP ? dt : ema * 0.85 + dt * 0.15;
    // ignore warm-up frames (shader linking, texture upload) and frames drawn while the tab is hidden
    if (document.hidden) nFrames = Math.min(nFrames, WARMUP);
    if (nFrames > WARMUP + 20 && ema > (tier === 2 ? 22 : 45)) degrade();
    const moving = anim || !paused || now - lastInteract < 1600;
    // controls.update() above may already have queued the next frame through its "change" event; queueing a second
    // one doubled the callbacks every frame (1 → 2 → 4 …) and was the growing 60 → 840 ms freeze in the intro
    if (visible && !document.hidden && moving && !raf) raf = requestAnimationFrame(frame);
  }
  function requestRender() { if (!raf && !disposed && !held) raf = requestAnimationFrame(frame); }
  let dragging = false, touched = false;
  controls.addEventListener("start", () => { dragging = true; touched = true; anim = null; lastInteract = performance.now(); requestRender(); });
  controls.addEventListener("end", () => { dragging = false; lastInteract = performance.now(); requestRender(); });
  controls.addEventListener("change", () => { if (dragging) lastInteract = performance.now(); requestRender(); });

  function resize() {
    const w = Math.max(1, container.clientWidth), h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false); camera.aspect = w / h;
    camera.fov = w / h < 1.1 ? 46 : 34;
    // wide hero (intro, sign-in): the airframe sits right of centre so it never runs into the wordmark on the left
    if (mode === "hero" && w / h > 1.05) camera.setViewOffset(w, h, -w * 0.12, 0, w, h); else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    if (!touched && !anim) {                         // not touched yet: keep the preset framed
      const P = PRESETS[current]; camera.position.copy(presetPos(P)); controls.target.fromArray(P.target);
      if (focusV) { controls.target.lerp(focusV, 0.6); camera.position.add(focusV.clone().multiplyScalar(0.6)); }
      controls.maxDistance = Math.max(85, fitFor(P) * 1.6); controls.update();
    }
    requestRender();
  }
  const ro = new ResizeObserver(resize); ro.observe(container); resize();
  const io = new IntersectionObserver((es) => { visible = es.some((e) => e.isIntersecting); if (visible) requestRender(); }, { threshold: 0.01 });
  io.observe(container);
  const onVis = () => { if (!document.hidden) requestRender(); };
  document.addEventListener("visibilitychange", onVis);
  const onContextLost = (event) => {
    event.preventDefault();
    if (disposed) return;
    ctl.destroy();
    opts.onError && opts.onError("3D rendering was interrupted — showing a simplified aircraft.");
  };
  renderer.domElement.addEventListener("webglcontextlost", onContextLost);

  // keyboard: arrows orbit, +/- zoom, 1–4 presets, space pauses (when the canvas has focus)
  const onKey = (e) => {
    const sph = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    let used = true;
    if (e.key === "ArrowLeft") sph.theta -= 0.12; else if (e.key === "ArrowRight") sph.theta += 0.12;
    else if (e.key === "ArrowUp") sph.phi = Math.max(controls.minPolarAngle, sph.phi - 0.1); else if (e.key === "ArrowDown") sph.phi = Math.min(controls.maxPolarAngle, sph.phi + 0.1);
    else if (e.key === "+" || e.key === "=") sph.radius = Math.max(controls.minDistance, sph.radius * 0.88); else if (e.key === "-" || e.key === "_") sph.radius = Math.min(controls.maxDistance, sph.radius * 1.12);
    else if (e.key === "1" || e.key === "0") goTo("three_quarter"); else if (e.key === "2") goTo("front"); else if (e.key === "3") goTo("side"); else if (e.key === "4") goTo("top");
    else if (e.key === " ") { ctl.setPaused(!paused); } else used = false;
    if (!used) return;
    e.preventDefault(); touched = true; if (!/^[0-4 ]$/.test(e.key)) { anim = null; camera.position.copy(new THREE.Vector3().setFromSpherical(sph).add(controls.target)); lastInteract = performance.now(); requestRender(); }
  };
  container.addEventListener("keydown", onKey);

  setZones(opts.zones, opts.focus);

  // NASA Global Hawk: fetched after the first frame on a hardware GPU, checked (size; sha256 where the browser exposes
  // crypto.subtle), parsed and validated, then swapped in. Any failure keeps the fighter concept and is reported.
  async function loadHawk() {
    try {
      const res = await fetch(MODEL.url, { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      if (buf.byteLength !== MODEL.bytes) throw new Error("unexpected file size");
      if (globalThis.crypto && crypto.subtle) {
        const hex = [...new Uint8Array(await crypto.subtle.digest("SHA-256", buf))].map((b) => b.toString(16).padStart(2, "0")).join("");
        if (hex !== MODEL.sha256) throw new Error("checksum mismatch");
      }
      if (disposed) return;
      const hawk = buildHawk(parseGLB(buf), mats);
      // compile the airframe's shader variants off the critical path before showing it: linking them synchronously on
      // the first draw was one of the long main-thread tasks behind the intro freeze
      if (renderer.compileAsync) { compiling = true; try { await renderer.compileAsync(hawk, camera, scene); } catch { /* drawn and linked normally */ } compiling = false; }
      if (disposed) { hawk.traverse((o) => o.geometry && o.geometry.dispose()); releaseRenderer(); return; }
      const old = aircraft, keep = new Set(Object.values(mats));
      scene.remove(old);
      old.traverse((o) => { o.geometry && o.geometry.dispose(); if (o.material && !keep.has(o.material)) [].concat(o.material).forEach((m) => m.dispose()); });
      clearTimeout(revealTimer);
      aircraft = hawk; scene.add(hawk); modelId = MODEL.id;
      zoneTable = Object.fromEntries(Object.entries(ZONES_GLOBAL_HAWK).map(([k, v]) => [k, hawkToScene(v)]));
      bound = new THREE.Box3().setFromObject(hawk).getBoundingSphere(new THREE.Sphere());
      const half = Math.ceil(bound.radius + 2);
      Object.assign(key.shadow.camera, { left: -half, right: half, top: half, bottom: -half }); key.shadow.camera.updateProjectionMatrix();
      refocus(); setZones(lastZones, lastFocus);
      if (anim) refit = true; else resize();
      opts.onModel && opts.onModel({ id: MODEL.id, credit: MODEL.credit, creditShort: MODEL.creditShort });
    } catch (e) {
      clearTimeout(revealTimer); if (aircraft) aircraft.visible = true; requestRender();
      console.warn("Global Hawk model unavailable, keeping the fighter concept:", e && e.message);
      opts.onModel && opts.onModel({ id: "fighter", reason: String((e && e.message) || e) });
    }
  }

  function releaseRenderer() {
    scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) [].concat(o.material).forEach((mm) => { if (mm.map) mm.map.dispose(); mm.dispose(); }); });
    envTex && envTex.dispose(); renderer.dispose();
  }
  const ctl = {
    preset: goTo,
    setPaused(v) { paused = !!v; lastInteract = performance.now(); requestRender(); opts.onPause && opts.onPause(paused); },
    get paused() { return paused; },
    /** Stop drawing while another layer (the intro) covers this view; the user's pause preference is untouched. */
    hold(v) { const was = held; held = !!v; if (was && !held) { lastInteract = performance.now(); requestRender(); } },
    setZones,
    destroy() {
      if (disposed) return;
      disposed = true; clearTimeout(revealTimer); if (raf) cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); document.removeEventListener("visibilitychange", onVis);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      container.removeEventListener("keydown", onKey); controls.dispose();
      // compileAsync polls shader programs. Keep GPU objects alive until that poll
      // finishes, even though listeners, animation and the DOM are detached now.
      renderer.domElement.remove();
      if (!compiling) releaseRenderer();
    },
    get tier() { return tier; },
    get model() { return modelId; },
    stats() { return { model: modelId, tier, frameMs: +ema.toFixed(1), markers: markers.map((m) => m.zone), paused, cam: camera.position.toArray().map((x) => +x.toFixed(1)), triangles: renderer.info.render.triangles, calls: renderer.info.render.calls, geometries: renderer.info.memory.geometries }; },
  };
  // compile shaders off the critical path (parallel compile where the driver supports it), then draw
  const ready = () => {
    if (compiled || disposed) return;
    compiled = true;
    if (opts.entrance && !reduced) {                      // intro: the aircraft rises out of the dark towards the three-quarter view
      const P = PRESETS[current], to = presetPos(P), from = to.clone().multiplyScalar(1.75).add(new THREE.Vector3(0, -6, 0));
      camera.position.copy(from);
      anim = { t0: performance.now(), dur: 2600, step: (k) => { const e = 1 - Math.pow(1 - k, 3); camera.position.lerpVectors(from, to, e); } };
    }
    requestRender(); opts.onFirstFrame && requestAnimationFrame(() => opts.onFirstFrame());
    // software rasterisers (tier 0) and explicit opt-outs keep the light fighter concept
    if (tier > 0 && opts.model !== "fighter") loadHawk(); else opts.onModel && opts.onModel({ id: "fighter", reason: tier > 0 ? "requested" : "software rendering" });
  };
  const finishCompile = () => { compiling = false; if (disposed) releaseRenderer(); else ready(); };
  (renderer.compileAsync ? renderer.compileAsync(scene, camera) : Promise.resolve()).then(finishCompile, finishCompile);
  if (tier === 0 && controls.autoRotate) { opts.onPause && opts.onPause(true); opts.onDegrade && opts.onDegrade("Simplified rendering · drag to turn"); }
  opts.onReady && opts.onReady(ctl);
  return ctl;
}
