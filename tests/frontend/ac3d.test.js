// Phase 14: the 3D aircraft only marks components the inspection data can name. Run: node --test tests/frontend/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "..", "apps", "web", "src");
const scene = fs.readFileSync(path.join(SRC, "modules/aircraft_scene.mjs"), "utf8");

function components() {
  const ctx = vm.createContext({ V: { services: { request: async () => ({}) } }, URLSearchParams, FormData, Blob, console });
  vm.runInContext(fs.readFileSync(path.join(SRC, "services/aero.js"), "utf8"), ctx);
  return ctx.V.services.aero.COMPONENTS.map(([id]) => id);
}
function zones() {
  const m = scene.match(/export const ZONES = (\{[\s\S]*?\});/);
  assert.ok(m, "ZONES export missing");
  return vm.runInNewContext("(" + m[1] + ")");
}

test("every marker zone is a recorded inspection component, and every specific component has a zone", () => {
  const z = Object.keys(zones()).sort(), c = components().filter((x) => x !== "other").sort();
  assert.equal(JSON.stringify(z), JSON.stringify(c));
});

test("'other' (not specified) never gets a position on the aircraft", () => {
  assert.equal(Object.prototype.hasOwnProperty.call(zones(), "other"), false);
});

test("zone anchors are finite 3-vectors inside the airframe envelope", () => {
  for (const [k, v] of Object.entries(zones())) {
    assert.equal(v.length, 3, k);
    assert.ok(v.every(Number.isFinite), k);
    assert.ok(Math.abs(v[0]) < 22 && Math.abs(v[1]) < 8 && Math.abs(v[2]) < 18, `${k} outside the aircraft: ${v}`);
  }
});

test("markers are only created for zones with confirmed or AI findings (plus the focused component)", () => {
  assert.match(scene, /if \(!confirmed && !ai\) continue;/);
  // zoneTable is ZONES (fighter) or the Global Hawk anchors once that model is shown
  assert.match(scene, /filter\(\(\[z\]\) => zoneTable\[z\]\)/);
  assert.match(scene, /let zoneTable = ZONES/);
});

test("the 3D module degrades instead of blocking: software-GL detection, adaptive quality and async shader compile", () => {
  assert.match(scene, /swiftshader\|llvmpipe/);
  assert.match(scene, /function degrade\(\)/);
  assert.match(scene, /compileAsync/);
  const ac3d = fs.readFileSync(path.join(SRC, "lib/ac3d.js"), "utf8");
  assert.match(ac3d, /prefers-reduced-motion/);
  assert.match(ac3d, /function fallback\(/);
});

test("the no-WebGL wireframe uses the same components and never places 'other'", () => {
  const src = fs.readFileSync(path.join(SRC, "lib/aircraft3d.js"), "utf8");
  const m = src.match(/const ZONE_AT = (\{[\s\S]*?\});/);
  const z = vm.runInNewContext("(" + m[1] + ")");
  assert.equal(Object.prototype.hasOwnProperty.call(z, "other"), false);
  assert.equal(JSON.stringify(Object.keys(z).sort()), JSON.stringify(Object.keys(zones()).sort()));
});

async function geometryHarness() {
  // Execute the actual geometry builders with the shipped Three.js runtime. No
  // renderer, DOM, network dependency or screenshot approximation is involved.
  const threeSrc = fs.readFileSync(path.join(SRC, "..", "vendor", "three", "three.module.min.js"), "utf8");
  const THREE = await import("data:text/javascript;base64," + Buffer.from(threeSrc).toString("base64"));
  const definitions = scene.replace(/^import .*;\r?$/gm, "").replace(/^export /gm, "");
  return vm.runInNewContext(`(() => { ${definitions}
    return { THREE, loft, wingSections, wingletSections, tailplaneSections, finSections, fighterSurfaces, buildAircraft, materials,
             MODEL, ZONES, ZONES_GLOBAL_HAWK, hawkToScene, parseGLB, buildHawk, GROUND_Y };
  })()`, { THREE, TextDecoder });
}

test("both wings, winglets, stabilisers and fin have outward-facing solid surfaces", async () => {
  const g = await geometryHarness();
  const surfaces = [1, -1].flatMap((side) => [
    ["wing " + side, g.wingSections(side)],
    ["winglet " + side, g.wingletSections(side)],
    ["tailplane " + side, g.tailplaneSections(side)],
  ]).concat([["fin", g.finSections()]]);
  for (const [name, sections] of surfaces) {
    const mesh = g.loft(sections);
    const normals = mesh.getAttribute("normal");
    // Mid-chord upper skin at the root must face the thickness direction,
    // otherwise front-face culling makes that solid surface disappear.
    const upper = new g.THREE.Vector3().fromBufferAttribute(normals, 11);
    const lower = new g.THREE.Vector3().fromBufferAttribute(normals, 33);
    assert.ok(upper.dot(sections[0].thickDir) > 0.7, `${name}: upper skin faces inward`);
    assert.ok(lower.dot(sections[0].thickDir) < -0.7, `${name}: lower skin faces inward`);
    mesh.dispose();
  }
});


test("fighter assembly has finite geometry, credible envelope and paired major structures", async () => {
  const g=await geometryHarness(), model=g.buildAircraft(g.materials(null,true));
  const names={}, bounds=new g.THREE.Box3().setFromObject(model); let triangles=0;
  model.traverse(o=>{if(!o.isMesh)return;names[o.name]=(names[o.name]||0)+1;
    const p=o.geometry.getAttribute("position");assert.ok(Array.from(p.array).every(Number.isFinite),o.name);
    triangles+=(o.geometry.index?.count||p.count)/3;
  });
  assert.equal(names.canopy,1);assert.equal(names["canted-fin"],2);assert.equal(names["exhaust-nozzle"],2);
  assert.equal(names["wing_starboard"],1);assert.equal(names["wing_port"],1);
  const size=bounds.getSize(new g.THREE.Vector3());assert.ok(size.x>19&&size.x<22);assert.ok(size.z>13&&size.z<15);
  assert.ok(triangles<50000,"illustrative model must stay within a modest triangle budget");
});
test("new fighter wings and canted fins have outward-facing skins on both sides",async()=>{
  const g=await geometryHarness();for(const side of [-1,1])for(const [name,sections] of Object.entries(g.fighterSurfaces(side))){
    const mesh=g.loft(sections),n=mesh.getAttribute("normal");
    assert.ok(new g.THREE.Vector3().fromBufferAttribute(n,11).dot(sections[0].thickDir)>.65,name+side);
    assert.ok(new g.THREE.Vector3().fromBufferAttribute(n,33).dot(sections[0].thickDir)<-.65,name+side);
  }
});

// ---------------------------------------------------------------- NASA Global Hawk reference model
const GLB = path.join(SRC, "..", "vendor", "models", "global_hawk.glb");
const glbBuffer = () => { const b = fs.readFileSync(GLB); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };

test("Global Hawk asset matches the recorded size and checksum and carries no image data (NASA insignia removed)", async () => {
  const g = await geometryHarness(), raw = fs.readFileSync(GLB);
  assert.equal(raw.length, g.MODEL.bytes);
  assert.equal(require("crypto").createHash("sha256").update(raw).digest("hex"), g.MODEL.sha256);
  const jsonLen = raw.readUInt32LE(12), json = JSON.parse(raw.subarray(20, 20 + jsonLen).toString("utf8"));
  for (const k of ["images", "textures", "samplers", "extensionsUsed", "extensionsRequired"]) assert.equal(json[k], undefined, k);
  assert.ok(json.meshes[0].primitives.every((p) => !("TEXCOORD_0" in p.attributes)), "no texture coordinates");
  // no embedded PNG / JPEG / WebP payloads anywhere in the file
  for (const sig of [Buffer.from([0x89, 0x50, 0x4e, 0x47]), Buffer.from([0xff, 0xd8, 0xff]), Buffer.from("WEBP")]) assert.equal(raw.indexOf(sig), -1, sig.toString("hex"));
  assert.match(json.asset.copyright, /NASA/); assert.match(json.asset.copyright, /No NASA endorsement/);
  assert.match(g.MODEL.credit, /NASA/);
  assert.ok(fs.readFileSync(path.join(SRC, "..", "vendor", "models", "NOTICE.txt"), "utf8").includes(g.MODEL.sha256));
});

test("parseGLB reads the shipped model exactly and rejects anything outside its narrow format", async () => {
  const g = await geometryHarness(), m = g.parseGLB(glbBuffer());
  assert.equal(m.parts.length, 6);
  assert.equal(m.parts.reduce((s, p) => s + p.index.length / 3, 0), 37120);
  for (const p of m.parts) { assert.ok(p.normal && p.normal.length === p.position.length); assert.ok(p.position.every(Number.isFinite)); }
  const bad = (mutate, re) => { const b = Buffer.from(fs.readFileSync(GLB)); mutate(b); assert.throws(() => g.parseGLB(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)), re); };
  bad((b) => b.write("glTX", 0), /not a glTF/);
  bad((b) => { const n = b.readUInt32LE(12), s = b.subarray(20, 20 + n).toString("utf8").replace('"scene":0', '"images":[]'); b.write(s.padEnd(n, " "), 20); }, /unexpected/);
});

test("Global Hawk assembly: credible envelope, nose forward, resting on the ground, within the triangle budget", async () => {
  const g = await geometryHarness(), hawk = g.buildHawk(g.parseGLB(glbBuffer()), g.materials(null, true));
  hawk.updateMatrixWorld(true);
  const box = new g.THREE.Box3().setFromObject(hawk), size = box.getSize(new g.THREE.Vector3());
  assert.ok(Math.abs(size.x - g.MODEL.length) < 0.05, `length ${size.x}`);           // fuselage along +x like the fighter
  assert.ok(size.z > 28 && size.z < 31, `span ${size.z}`);                            // span / length = 2.035 in the source
  assert.ok(Math.abs(box.min.y - g.GROUND_Y) < 0.01, "gear on the ground");
  let tris = 0, nose = null;
  hawk.traverse((o) => { if (!o.isMesh) return; tris += o.geometry.index.count / 3;
    const p = o.geometry.getAttribute("position"), v = new g.THREE.Vector3();
    for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld); if (!nose || v.x > nose.x) nose = v.clone(); } });
  assert.equal(tris, 37120);
  assert.ok(Math.abs(nose.z) < 0.6, "the most forward point is the nose on the centreline");
  const kinds = new Set(); hawk.traverse((o) => o.isMesh && kinds.add(o.material)); assert.ok(kinds.size <= 5 && [...kinds].every((m) => !m.map), "app materials, no textures");
});

test("Global Hawk zone anchors cover the same components and sit on the mesh", async () => {
  const g = await geometryHarness();
  assert.equal(JSON.stringify(Object.keys(g.ZONES_GLOBAL_HAWK).sort()), JSON.stringify(Object.keys(g.ZONES).sort()));
  const m = g.parseGLB(glbBuffer());
  for (const [k, a] of Object.entries(g.ZONES_GLOBAL_HAWK)) {
    let best = Infinity;
    for (const p of m.parts) for (let i = 0; i < p.position.length; i += 3) best = Math.min(best, Math.hypot(p.position[i] - a[0], p.position[i + 1] - a[1], p.position[i + 2] - a[2]));
    assert.ok(best < 0.03, `${k} is ${best.toFixed(3)} model units from the surface`);
    const s = g.hawkToScene(a); assert.ok(s.every(Number.isFinite) && Math.abs(s[2]) < 16, k);
  }
});

test("the Global Hawk is optional: loaded after the first frame on hardware GL, verified, and any failure keeps the fighter", () => {
  assert.match(scene, /if \(tier > 0 && opts\.model !== "fighter"\) loadHawk\(\)/);
  assert.match(scene, /buf\.byteLength !== MODEL\.bytes/);
  assert.match(scene, /hex !== MODEL\.sha256/);
  assert.match(scene, /keeping the fighter concept/);
  const ac3d = fs.readFileSync(path.join(SRC, "lib/ac3d.js"), "utf8");
  assert.match(ac3d, /onModel: \(m\) =>/);
  assert.match(ac3d, /data-ac3d-credit/);
});

test('mounted aircraft accessible label follows evidence updates without replacing the canvas', () => {
  const labels = {};
  const stage = { setAttribute: (key,value) => labels[key]=value };
  const element = { dataset: {label:'3D aircraft. 1 confirmed, 7 AI proposals',zones:'{}'}, classList:{contains:()=>false}, querySelector:()=>stage, _ac:{setZones:()=>{}}, _zonesSig:'{}' };
  const lib = { on:()=>{} };
  vm.runInNewContext(fs.readFileSync(path.join(SRC,'lib/ac3d.js'),'utf8'), {V:{lib}});
  lib.ac3dMount({querySelectorAll:()=>[element]});
  assert.match(labels['aria-label'], /1 confirmed, 7 AI proposals/);
  element.dataset.label='3D aircraft. No defects recorded yet';
  lib.ac3dMount({querySelectorAll:()=>[element]});
  assert.match(labels['aria-label'], /No defects recorded yet/);
});
