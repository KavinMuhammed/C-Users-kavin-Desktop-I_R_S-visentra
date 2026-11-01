// Phase 12 frontend benchmark (Chromium via Playwright) against a running development server.
//   VISENTRA_URL=http://127.0.0.1:8820 BENCH_EMAIL=… BENCH_PW=… node scripts/perf/frontend_bench.js [runs]
// Per page (cold load, cache disabled): DOMContentLoaded, first contentful paint, time until the page's main
// content is rendered, JS heap after load, request count and transferred bytes. Then 5 s idle on the
// dashboard: main-thread task/script time and animation frames, from the Chrome DevTools Protocol.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const BASE = process.env.VISENTRA_URL || 'http://127.0.0.1:8820';
const RUNS = +(process.argv[2] || 5);
const PAGES = [['/inspection', '#main h1'], ['/inspection/history', '[data-server-section], #main h1'], ['/inspection/review', '.rv-queue'], ['/inspection/datasets', '#main h1']];
const med = (a) => { const s = a.slice().sort((x, y) => x - y); return s[Math.floor((s.length - 1) / 2)]; };
(async () => {
  const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.route(/fonts\.(googleapis|gstatic)/, (r) => r.abort());
  await page.goto(BASE + '/#/login'); await page.waitForTimeout(800);
  await page.fill('#login-email', process.env.BENCH_EMAIL); await page.fill('#login-password', process.env.BENCH_PW || 'visentra-bench-2026');
  await page.click('form[data-act=auth-login] button[type=submit]'); await page.waitForTimeout(2000);
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable'); await cdp.send('Network.setCacheDisabled', { cacheDisabled: true }); await cdp.send('Performance.enable');
  const out = { base: BASE, runs: RUNS, pages: {}, idle: null };
  for (const [route, sel] of PAGES) {
    const r = { dcl: [], fcp: [], content: [], heapMB: [], requests: [], kB: [] };
    for (let i = 0; i < RUNS; i++) {
      let n = 0, bytes = 0;
      const onResp = (e) => { n++; bytes += e.encodedDataLength || 0; };
      cdp.on('Network.loadingFinished', onResp);
      const t0 = Date.now();
      await page.goto(BASE + '/#' + route, { waitUntil: 'domcontentloaded' });
      await page.reload({ waitUntil: 'domcontentloaded' });
      const t1 = Date.now();
      await page.waitForSelector(sel, { timeout: 30000 });
      await page.waitForFunction(() => !document.querySelector('[aria-busy="true"], .skeleton'), null, { timeout: 30000 }).catch(() => {});
      const tc = Date.now();
      await page.waitForTimeout(400);
      cdp.off('Network.loadingFinished', onResp);
      const nav = await page.evaluate(() => { const n = performance.getEntriesByType('navigation')[0]; const p = performance.getEntriesByName('first-contentful-paint')[0]; return { dcl: n.domContentLoadedEventEnd, fcp: p ? p.startTime : null }; });
      const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
      r.dcl.push(nav.dcl); r.fcp.push(nav.fcp); r.content.push(tc - t1 + nav.dcl); r.heapMB.push(m.JSHeapUsedSize / 1048576); r.requests.push(n); r.kB.push(bytes / 1024);
      void t0;
    }
    out.pages[route] = Object.fromEntries(Object.entries(r).map(([k, v]) => [k, Math.round(med(v) * 10) / 10]));
  }
  await page.goto(BASE + '/#/inspection'); await page.waitForTimeout(2000);
  const g = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
  const frames = await page.evaluate(() => new Promise((res) => { let f = 0; const t = performance.now(); const tick = () => { f++; if (performance.now() - t < 5000) requestAnimationFrame(tick); else res(f); }; requestAnimationFrame(tick); }));
  const a = await g(); await page.waitForTimeout(5000); const z = await g();
  out.idle = { seconds: 5, task_ms: Math.round((z.TaskDuration - a.TaskDuration) * 1000), script_ms: Math.round((z.ScriptDuration - a.ScriptDuration) * 1000), layout_ms: Math.round((z.LayoutDuration - a.LayoutDuration) * 1000), raf_frames_in_5s_probe: frames };
  // a later window with no input at all (15–20 s after load): decorative canvases should be stopped by then
  await page.waitForTimeout(3000); const a2 = await g(); await page.waitForTimeout(5000); const z2 = await g();
  out.idle_after_15s = { seconds: 5, task_ms: Math.round((z2.TaskDuration - a2.TaskDuration) * 1000), script_ms: Math.round((z2.ScriptDuration - a2.ScriptDuration) * 1000) };
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
