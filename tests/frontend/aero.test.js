// Phase 13: inspection API client (services/aero.js). Run: node --test tests/frontend/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "..", "apps", "web", "src");
function load() {
  const calls = [];
  const ctx = vm.createContext({ V: { services: { request: async (...a) => { calls.push(a); return {}; } } }, URLSearchParams, FormData, Blob, console });
  vm.runInContext(fs.readFileSync(path.join(SRC, "services/aero.js"), "utf8"), ctx, { filename: "services/aero.js" });
  return { A: ctx.V.services.aero, calls };
}

test("defect classes are exactly the review taxonomy, with an 'other' fallback", () => {
  const { A } = load();
  assert.equal(JSON.stringify(A.DEFECTS.map((d) => d.id)), JSON.stringify(["crack", "dent", "corrosion", "other"]));
  assert.equal(A.defect("rust").id, "other");
  assert.equal(A.component("other"), "Not specified");
  assert.equal(A.component("wing_upper"), "Wing — upper");
});

test("create always asks the server to analyse and sends an idempotency key", async () => {
  const { A, calls } = load();
  await A.create(new Blob(["x"], { type: "image/jpeg" }), { aircraft_ref: "VT-ANX", component: "fuselage", zone: "" }, "k-1");
  const [method, url, opts] = calls[0];
  assert.equal(method, "POST"); assert.equal(url, "/api/v1/inspections"); assert.equal(opts.idempotent, "k-1");
  assert.equal(opts.form.get("analyse"), "true"); assert.equal(opts.form.get("aircraft_ref"), "VT-ANX"); assert.equal(opts.form.get("zone"), null);
});

test("review mutations carry the inspection revision in If-Match", async () => {
  const { A, calls } = load();
  await A.decide("i1", 7, { target: { type: "prediction", id: "p" }, kind: "decision", decision: "rejected" });
  await A.signoff("i1", 8, "ok");
  await A.analyse("i1", 3);
  assert.deepEqual(calls.map((c) => [c[1], c[2].headers["If-Match"]]), [["/api/v1/inspections/i1/decisions", '"7"'], ["/api/v1/inspections/i1/signoff", '"8"'], ["/api/v1/inspections/i1/analyses", '"3"']]);
  assert.equal(JSON.stringify(calls[1][2].json), JSON.stringify({ note: "ok" }));
});

test("list builds a query string without empty filters; ids are URL-encoded", async () => {
  const { A, calls } = load();
  await A.list({ q: "VT-ANX", status: "", page: 2 });
  await A.get("a/b");
  assert.equal(calls[0][1], "/api/v1/inspections?q=VT-ANX&page=2");
  assert.equal(calls[1][1], "/api/v1/inspections/a%2Fb");
});

test("text evidence reads scoped inspection and corrections carry evidence revision and confirmation", async () => {
  const { A, calls } = load();
  await A.textEvidence("i/a");
  await A.correctText("i/a", "e/b", 7, "Checked text");
  assert.equal(calls[0][1], "/api/v1/inspections/i%2Fa/text-evidence");
  assert.equal(calls[1][0], "PATCH");
  assert.equal(calls[1][1], "/api/v1/inspections/i%2Fa/text-evidence/e%2Fb");
  assert.equal(calls[1][2].headers["If-Match"], '"7"');
  assert.equal(JSON.stringify(calls[1][2].json), JSON.stringify({text: "Checked text", confirmed: true}));
});

function evidenceView() {
  const ui = { dialogs: {}, btn: (p) => `<button type="${p.type || 'button'}">${p.label}</button>` };
  const lib = { esc: (v) => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
    on() {}, debounce: (fn) => fn, router: { add() {} }, fmt: {}, icon() {} };
  const ctx = vm.createContext({ V: { features: { aeroViewer: {} }, ui, lib, services: { aero: {} } }, document: { addEventListener() {} }, console });
  vm.runInContext(fs.readFileSync(path.join(SRC, 'features/aero_pages.js'), 'utf8'), ctx);
  return ui.dialogs['aw-text'];
}
const evidenceRow = { id: 'e1', filename: '<script>source</script>', status: 'completed', engine: 'OCR', pages: [{page:1,method:'ocr'}], sha256:'abc',
  original_text: '<img onerror=bad>', corrected_text:'Checked value', confirmed_by:'reviewer', confirmed_at:'now', revision:3 };

test('OCR view escapes source text and keeps originals separate from confirmed corrections', () => {
  const html = evidenceView()({id:'i1', insp:{review_permissions:{review:true},allowed_actions:['read_original']}, items:[evidenceRow]}).body;
  assert.ok(html.includes('&lt;img onerror=bad&gt;') && !html.includes('<script>'));
  assert.ok(html.includes('Original extracted text') && html.includes('Confirmed correction'));
  assert.ok(html.includes('1: ocr') && html.includes('SHA-256: abc'));
  assert.ok(html.includes('data-rev="3"') && html.includes('name="confirmed" required'));
});
test('OCR editing and source download respect review and original-source permissions', () => {
  const render = evidenceView();
  for (const insp of [{status:'reviewed',review_permissions:{review:true}}, {status:'in_review',review_permissions:{review:false}}, {}]) {
    const html = render({id:'i1',insp,items:[evidenceRow]}).body;
    assert.ok(!html.includes('aw-text-save') && !html.includes('Download source'));
  }
  assert.ok(!render({id:'i1',insp:{review_permissions:{review:true}},items:[{...evidenceRow,status:'failed',error_code:'OCR_FAILED'}]}).body.includes('aw-text-save'));
});

function pagesModule() {
  const ui = { dialogs: {} };
  const lib = { esc: (v) => String(v ?? ''), on() {}, debounce: (fn) => fn, router: { add() {} }, fmt: {}, icon() {} };
  const ctx = vm.createContext({ V: { features: { aeroViewer: {} }, ui, lib, services: { aero: {} } }, document: { addEventListener() {} }, console });
  vm.runInContext(fs.readFileSync(path.join(SRC, 'features/aero_pages.js'), 'utf8'), ctx);
  return ctx.V.features.aero;
}
const pred = (o) => ({ id: 'p', kind: 'prediction', cls: 'dent', predicted: 'dent', score: 0.6, decision: 'pending', ...o });

test('Phase 4: guidance for an unreviewed AI class is provisional until an engineer confirms or corrects it', () => {
  const P = pagesModule();
  assert.equal(P.guidanceBasis([pred()], 'dent').provisional, true);
  const fr = P.guidanceBasis([pred({ decision: 'further_review' })], 'dent');
  assert.equal(fr.provisional, true); assert.equal(fr.further, true);
  assert.equal(P.guidanceBasis([pred({ decision: 'confirmed' })], 'dent').provisional, false);
  // a stain proposed as a dent and reclassified by the engineer: the corrected class is established, the proposed one is not
  const corrected = pred({ cls: 'other', decision: 'confirmed' });
  assert.equal(P.guidanceBasis([corrected], 'other').provisional, false);
  assert.equal(P.guidanceBasis([corrected], 'dent').provisional, true);
  // an engineer's own finding is established; a selected pending box stays provisional even if another box was confirmed
  assert.equal(P.guidanceBasis([{ id: 'h', kind: 'human_finding', cls: 'crack', decision: 'confirmed' }], 'crack').provisional, false);
  const sel = pred({ id: 'p2' });
  assert.equal(P.guidanceBasis([pred({ decision: 'confirmed' }), sel], 'dent', sel).provisional, true);
});

// Scientific review 2026-10-29: the proposal panel names the three review stages in words and never marks a disposition as done.
function pagesWithDefects() {
  const ui = { dialogs: {} };
  const lib = { esc: (v) => String(v ?? ''), on() {}, debounce: (fn) => fn, router: { add() {} }, fmt: {}, icon() {} };
  const aero = { defect: (c) => ({ label: { dent: 'Dent', crack: 'Crack', corrosion: 'Corrosion' }[c] || 'Other' }) };
  const ctx = vm.createContext({ V: { features: { aeroViewer: {} }, ui, lib, services: { aero } }, document: { addEventListener() {} }, console });
  vm.runInContext(fs.readFileSync(path.join(SRC, 'features/aero_pages.js'), 'utf8'), ctx);
  return ctx.V.features.aero;
}
const prop = (inspector, ai = { class: 'crack', score: 0.6 }) => ({ stages: { ai, inspector, disposition: { status: 'not_established' } } });

test('proposal stages keep the AI class, the inspector decision and the disposition apart', () => {
  const P = pagesWithDefects();
  const pending = P.stages(prop({ status: 'not_reviewed', class: null, ai_class: 'crack' }));
  assert.equal(pending[0].state, 'Crack · model score 0.60'); assert.equal(pending[1].state, 'Not reviewed yet'); assert.equal(pending[1].done, false);
  const corrected = P.stages(prop({ status: 'corrected', class: 'dent', ai_class: 'crack', by: { name: 'R. Reviewer' } }));
  assert.equal(corrected[1].state, 'Corrected to Dent by R. Reviewer (AI: Crack)'); assert.equal(corrected[1].tag, 'Human decision');
  for (const s of [pending, corrected]) { assert.equal(s[2].state, 'Not established'); assert.equal(s[2].done, false); }
  const human = P.stages(prop({ status: 'recorded_by_reviewer', class: 'corrosion', by: { name: 'E' } }, null));
  assert.equal(human[0].tag, 'Not applicable'); assert.equal(human[1].state, 'Corrosion, recorded by E');
});

test('unknowns are split into not-derivable-from-image and not-yet-recorded', () => {
  const P = pagesWithDefects();
  const u = P.splitUnknowns(['Not derivable from the image: material loss', 'Not recorded in VisionTra: aircraft identity', 'something else']);
  assert.deepEqual([...u.image], ['Material loss']); assert.deepEqual([...u.record], ['Aircraft identity']); assert.deepEqual([...u.other], ['something else']);
});
