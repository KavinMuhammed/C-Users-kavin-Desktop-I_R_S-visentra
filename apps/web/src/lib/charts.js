/* ==========================================================================
   lib/charts.js — dependency-free SVG charts (scaled, themed, with tooltips)
   ========================================================================== */
(function (lib) {
  const esc = lib.esc;
  const niceMax = (v) => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; };
  lib.niceMax = niceMax;

  /** Line / area chart. series: [{name, color, values}] labels: string[] */
  lib.lineChart = ({ series, labels, height = 220, yFormat = (v) => lib.fmt.compact(v), yMax, yMin = 0, area = true, ticks = 4, xEvery }) => {
    const W = 640, H = height, L = 44, R = 12, T = 12, B = 26;
    const all = series.flatMap((s) => s.values.filter((v) => v != null));
    const intOnly = yMax == null && all.every((v) => Number.isInteger(v));
    const max = yMax != null ? yMax : intOnly ? Math.max(1, Math.ceil(Math.max(...all, 0) * 1.05)) : niceMax(Math.max(...all, 0) * 1.05);
    if (intOnly && max < ticks) ticks = max;
    const n = labels.length;
    const x = (i) => L + (n <= 1 ? 0 : (i / (n - 1)) * (W - L - R));
    const y = (v) => T + (1 - (v - yMin) / (max - yMin || 1)) * (H - T - B);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(series.map((q) => q.name).join(", "))} chart" style="height:${H}px">`;
    for (let i = 0; i <= ticks; i++) { const v = yMin + ((max - yMin) * i) / ticks; const yy = y(v); s += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 8}" y="${yy + 4}" text-anchor="end">${esc(yFormat(v))}</text>`; }
    const every = xEvery || Math.max(1, Math.ceil(n / 7));
    labels.forEach((lb, i) => { if (i % every === 0 || i === n - 1) s += `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(lb)}</text>`; });
    series.forEach((se) => {
      const pts = se.values.map((v, i) => (v == null ? null : [x(i), y(v)])).filter(Boolean);
      if (!pts.length) return;
      const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
      if (area && series.length <= 2) s += `<path d="${d} L${pts[pts.length - 1][0]} ${y(yMin)} L${pts[0][0]} ${y(yMin)} Z" fill="${se.color}" opacity=".10"/>`;
      s += `<path d="${d}" fill="none" stroke="${se.color}" stroke-width="2" vector-effect="non-scaling-stroke" ${se.dash ? 'stroke-dasharray="5 4"' : ""}/>`;
      const last = pts[pts.length - 1]; s += `<circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${se.color}" stroke="var(--surface)" stroke-width="2"/>`;
    });
    // hover columns
    const cw = (W - L - R) / Math.max(1, n - 1);
    labels.forEach((lb, i) => { const tip = lb + " · " + series.map((se) => `${se.name}: ${se.values[i] == null ? "—" : yFormat(se.values[i])}`).join(" · "); s += `<rect x="${x(i) - cw / 2}" y="${T}" width="${cw}" height="${H - T - B}" fill="transparent" data-tip="${esc(tip)}"/>`; });
    return s + "</svg>";
  };

  /** Vertical bars, optionally stacked. bars: [{label, values:[...]}], keys:[{name,color}] */
  lib.barChart = ({ bars, keys, height = 200, yFormat = (v) => lib.fmt.compact(v), ticks = 4, xEvery }) => {
    const W = 640, H = height, L = 44, R = 8, T = 10, B = 26;
    const totals = bars.map((b) => b.values.reduce((a, v) => a + v, 0));
    const intOnly = bars.every((b) => b.values.every((v) => Number.isInteger(v)));
    const max = intOnly && Math.max(...totals, 0) < 8 ? Math.max(1, Math.max(...totals, 0)) : niceMax(Math.max(...totals, 0));
    if (intOnly && max < ticks) ticks = max;
    const n = bars.length, bw = (W - L - R) / n, gap = Math.min(6, bw * 0.25);
    const y = (v) => T + (1 - v / max) * (H - T - B);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Bar chart" style="height:${H}px">`;
    for (let i = 0; i <= ticks; i++) { const v = (max * i) / ticks, yy = y(v); s += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 8}" y="${yy + 4}" text-anchor="end">${esc(yFormat(v))}</text>`; }
    const every = xEvery || Math.max(1, Math.ceil(n / 8));
    bars.forEach((b, i) => {
      let acc = 0; const bx = L + i * bw + gap / 2;
      b.values.forEach((v, k) => { if (!v) return; const y0 = y(acc), y1 = y(acc + v); s += `<rect x="${bx}" y="${y1}" width="${Math.max(1, bw - gap)}" height="${Math.max(0, y0 - y1)}" fill="${keys[k].color}" rx="${keys.length === 1 ? 2 : 0}"/>`; acc += v; });
      if (i % every === 0 || i === n - 1) s += `<text x="${bx + (bw - gap) / 2}" y="${H - 6}" text-anchor="middle">${esc(b.label)}</text>`;
      const tip = b.label + " · " + keys.map((k, j) => `${k.name}: ${yFormat(b.values[j] || 0)}`).join(" · ");
      s += `<rect x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent" data-tip="${esc(tip)}"/>`;
    });
    return s + "</svg>";
  };

  /** Inline sparkline */
  lib.spark = (values, color = "var(--accent)", w = 96, h = 28) => {
    if (!values.length) return "";
    const max = Math.max(...values, 1), min = Math.min(...values, 0);
    const pts = values.map((v, i) => [(i / (values.length - 1 || 1)) * (w - 4) + 2, h - 3 - ((v - min) / (max - min || 1)) * (h - 6)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const last = pts[pts.length - 1];
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d} L${last[0]} ${h} L2 ${h}Z" fill="${color}" opacity=".12"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"/><circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="${color}"/></svg>`;
  };

  /* global tooltip for [data-tip] */
  let tipEl = null;
  lib.bindTooltips = () => {
    document.addEventListener("mousemove", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (!t) { if (tipEl) tipEl.hidden = true; return; }
      if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "tooltip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
      tipEl.hidden = false; tipEl.textContent = t.getAttribute("data-tip");
      const r = tipEl.getBoundingClientRect(); let x = e.clientX + 12, y = e.clientY + 14;
      if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 12; if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 12;
      tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
    });
  };
})(V.lib);
