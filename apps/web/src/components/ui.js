/* ==========================================================================
   components/ui.js — reusable presentational components (HTML builders)
   ========================================================================== */
(function (ui, lib) {
  const esc = lib.esc, I = lib.icon, fmt = lib.fmt;
  const dataAttrs = (d) => Object.entries(d || {}).filter(([, v]) => v != null).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
  ui.dataAttrs = dataAttrs;

  ui.btn = ({ label = "", icon, iconRight, variant = "", size = "", act, data, disabled, title, type = "button", id, kbd, busy, cls = "", form, href } = {}) =>
    href ? `<a class="btn ${variant} ${size} ${cls}" href="${esc(href)}" ${title ? `title="${esc(title)}"` : ""} download>${icon ? I(icon) : ""}${label ? `<span>${esc(label)}</span>` : ""}</a>`
      : `<button type="${type}" class="btn ${variant} ${size} ${busy ? "is-busy" : ""} ${!label ? "icon" : ""} ${cls}" ${id ? `id="${id}"` : ""} ${form ? `form="${form}"` : ""} ${act ? `data-act="${act}"` : ""} ${dataAttrs(data)} ${disabled ? "disabled" : ""} ${title ? `title="${esc(title)}"` : ""} ${!label && title ? `aria-label="${esc(title)}"` : ""} ${busy ? 'aria-busy="true"' : ""}>${icon ? I(icon) : ""}${label ? `<span>${esc(label)}</span>` : ""}${kbd ? `<span class="kbd">${esc(kbd)}</span>` : ""}${iconRight ? I(iconRight) : ""}</button>`;
  ui.iconBtn = (icon, label, act, data, o = {}) => ui.btn({ icon, title: label, act, data, variant: o.variant || "ghost", size: o.size || "", disabled: o.disabled, cls: o.cls || "" });
  ui.badge = (text, tone = "", icon) => `<span class="badge ${tone}">${icon ? I(icon) : '<span class="dot" aria-hidden="true"></span>'}${esc(text)}</span>`;
  /** Provenance chip: tells the viewer where a number comes from. */
  ui.prov = (text, tone = "live") => `<span class="prov ${tone}">${tone === "live" ? '<span class="pulse" aria-hidden="true"></span>' : I("info")}${esc(text)}</span>`;
  ui.na = (text = "No data available") => `<span class="na">${esc(text)}</span>`;
  ui.avatar = (name, cls = "") => `<span class="avatar ${cls}" aria-hidden="true">${esc(fmt.initials(name || "?"))}</span>`;
  ui.time = (t) => (t ? `<time datetime="${esc(t)}" title="${esc(fmt.dateTime(t))}">${esc(fmt.rel(t))}</time>` : ui.na("—"));
  ui.kbd = (k) => `<span class="kbd">${esc(k)}</span>`;
  ui.mono = (t) => `<span class="mono">${esc(t)}</span>`;

  ui.field = ({ id, label, hint, error, control }) => `<div class="field">${label ? `<label for="${id}">${esc(label)}</label>` : ""}${control}${error ? `<div class="err-text" id="${id}-err" role="alert">${I("alert_circle")}${esc(error)}</div>` : hint ? `<div class="hint" id="${id}-hint">${hint}</div>` : ""}</div>`;
  ui.input = ({ id, name, type = "text", value = "", placeholder = "", error, autocomplete, cls = "", act, on = "input", min, max, step, disabled, attrs = "", required }) =>
    `<input class="input ${cls}" id="${id}" name="${name || id}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${required ? "required" : ""} ${autocomplete ? `autocomplete="${autocomplete}"` : ""} ${error ? `aria-invalid="true" aria-describedby="${id}-err"` : ""} ${act ? `data-act="${act}" data-on="${on}"` : ""} ${min != null ? `min="${min}"` : ""} ${max != null ? `max="${max}"` : ""} ${step != null ? `step="${step}"` : ""} ${disabled ? "disabled" : ""} ${attrs}>`;
  ui.select = ({ id, name, options, value, act, cls = "", label, disabled, attrs = "" }) =>
    `<select class="select ${cls}" id="${id}" name="${name || id}" ${act ? `data-act="${act}" data-on="change"` : ""} ${label ? `aria-label="${esc(label)}"` : ""} ${disabled ? "disabled" : ""} ${attrs}>${options.map((o) => { const [v, t] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(value ?? "") ? "selected" : ""}>${esc(t)}</option>`; }).join("")}</select>`;
  ui.search = ({ id, value = "", placeholder = "Search", act, attrs = "" }) => `<div class="input-wrap">${I("search")}<input class="input sm" type="search" id="${id}" value="${esc(value)}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" data-act="${act}" data-on="input" autocomplete="off" ${attrs}></div>`;
  ui.seg = (items, value, act, label, attrs = "") => `<div class="seg" role="group" aria-label="${esc(label || "")}">${items.map(([v, t, ic]) => `<button type="button" data-act="${act}" data-v="${esc(v)}" ${attrs} aria-pressed="${String(v) === String(value)}">${ic ? I(ic) : ""}${esc(t)}</button>`).join("")}</div>`;
  ui.switch = ({ id, label, checked, act, disabled, name }) => `<label class="switch" for="${id}"><input type="checkbox" role="switch" id="${id}" ${name ? `name="${name}"` : ""} ${checked ? "checked" : ""} data-controlled ${act ? `data-act="${act}" data-on="change"` : ""} ${disabled ? "disabled" : ""}><span>${esc(label)}</span></label>`;

  ui.pageHeader = ({ title, sub, actions = "", eyebrow }) => `<div class="page-h"><div class="col" style="gap:4px">${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ""}<h1>${esc(title)}</h1>${sub ? `<p class="muted">${sub}</p>` : ""}</div><div class="row wrap">${actions}</div></div>`;
  ui.card = ({ title, sub, actions = "", body = "", pad = true, cls = "", foot = "", id }) => `<section class="card ${cls}" ${id ? `id="${id}"` : ""}>${title ? `<div class="card-h"><div class="col" style="gap:2px;min-width:0"><h3>${title}</h3>${sub ? `<span class="xs muted">${sub}</span>` : ""}</div><div class="row wrap">${actions}</div></div>` : ""}${pad ? `<div class="card-b">${body}</div>` : body}${foot}</section>`;
  ui.metric = ({ label, value, foot = "", icon, act, data, spark = "" }) => `<div class="card metric ${act ? "click" : ""}" ${act ? `data-act="${act}" ${dataAttrs(data)} role="button" tabindex="0"` : ""}><div class="m-label">${icon ? I(icon) : ""}${esc(label)}</div><div class="row between top"><div class="m-value">${value}</div>${spark}</div><div class="m-foot">${foot}</div></div>`;
  ui.tabs = (tabs, active, act, label = "Sections") => `<div class="tabs" role="tablist" aria-label="${esc(label)}">${tabs.map((t) => `<button class="tab" role="tab" aria-selected="${t.id === active}" data-act="${act}" data-tab="${t.id}" tabindex="${t.id === active ? 0 : -1}">${t.icon ? I(t.icon) : ""}${esc(t.label)}${t.count != null ? `<span class="count">${t.count}</span>` : ""}</button>`).join("")}</div>`;
  ui.kv = (pairs) => `<dl class="kv">${pairs.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

  ui.empty = ({ icon = "folder", title, text = "", action = "" }) => `<div class="state"><div class="state-ic">${I(icon, "lg")}</div><h3>${esc(title)}</h3>${text ? `<p>${text}</p>` : ""}${action ? `<div class="row wrap" style="justify-content:center">${action}</div>` : ""}</div>`;
  ui.errorState = (err, retryKey) => {
    const e = err || {};
    if (e.code === "NOT_FOUND") return ui.empty({ icon: "search", title: "Not found", text: esc(e.message || "This item doesn't exist or you don't have access to it."), action: ui.btn({ label: "Go to overview", act: "nav", data: { to: "/" } }) });
    if (e.code === "FORBIDDEN") return `<div class="state warn" role="alert"><div class="state-ic">${I("lock", "lg")}</div><h3>Not allowed</h3><p>${esc(e.message)}</p></div>`;
    const net = e.code === "NETWORK";
    return `<div class="state bad" role="alert"><div class="state-ic">${I(net ? "wifi_off" : "alert", "lg")}</div><h3>${net ? "Server unreachable" : "Couldn't load this"}</h3><p>${esc(e.message || "Unexpected error.")}${e.request_id ? ` <span class="mono xs">ref ${esc(e.request_id)}</span>` : ""}</p>${ui.btn({ label: "Retry", icon: "refresh", act: "query-retry", data: { key: retryKey || "" }, variant: "primary" })}</div>`;
  };
  ui.skeleton = (kind = "page") => {
    const line = (w, h = 12) => `<div class="skel" style="width:${w};height:${h}px"></div>`;
    if (kind === "table") return `<div class="col gap-3" style="padding:16px" aria-busy="true" aria-label="Loading">${Array.from({ length: 6 }, () => `<div class="row gap-4">${line("40px", 32)}${line("30%")}${line("15%")}${line("20%")}</div>`).join("")}</div>`;
    if (kind === "grid") return `<div class="img-grid" aria-busy="true" aria-label="Loading">${Array.from({ length: 12 }, () => `<div class="skel" style="aspect-ratio:1"></div>`).join("")}</div>`;
    if (kind === "card") return `<div class="card pad col gap-3" aria-busy="true">${line("40%", 14)}${line("100%", 120)}</div>`;
    return `<div class="col gap-4" aria-busy="true" aria-label="Loading">${line("30%", 26)}<div class="grid g-3">${Array.from({ length: 3 }, () => `<div class="card pad col gap-3">${line("50%")}${line("70%", 24)}</div>`).join("")}</div><div class="card pad">${line("100%", 200)}</div></div>`;
  };
  ui.q = (entry, render, { skeleton = "page", key } = {}) => {
    if (entry.status === "loading") return ui.skeleton(skeleton);
    if (entry.status === "error") return ui.errorState(entry.error, key);
    try { return render(entry.data); } catch (e) { console.error(e); return ui.errorState({ message: "This section failed to render." }, key); }
  };
  ui.staleNote = (entry) => (entry.error && entry.status === "success" ? ui.banner("warn", "wifi_off", `Showing data from ${fmt.rel(entry.at)}. Refresh failed: ${esc(entry.error.message)}`) : "");
  ui.banner = (tone, icon, html, action = "") => `<div class="banner ${tone}" role="${tone === "bad" ? "alert" : "status"}">${I(icon)}<div class="grow">${html}</div>${action}</div>`;
  ui.progress = (v, tone = "", label = "Progress") => `<div class="progress ${tone} ${v == null ? "indeterminate" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" ${v != null ? `aria-valuenow="${Math.round(v * 100)}"` : ""} aria-label="${esc(label)}"><span style="width:${v == null ? 35 : Math.round(lib.clamp(v, 0, 1) * 1000) / 10}%"></span></div>`;

  /* ---------- domain ---------- */
  ui.confPill = (c, th) => c >= th ? `<span class="conf-pill ok">${I("check_circle")}Above threshold</span>` : `<span class="conf-pill low">${I("alert")}Low confidence — review recommended</span>`;
  ui.confText = (c, th) => `<span class="row" style="gap:6px"><span class="num" style="font-weight:600">${fmt.pct(c)}</span>${th != null && c < th ? `<span class="badge warn" title="Below the project threshold of ${fmt.pct(th, 0)}">${I("alert")}Low</span>` : ""}</span>`;
  ui.topK = (topk) => `<div class="col gap-3">${(topk || []).map((t, i) => `<div class="cbar ${i === 0 ? "top" : ""}"><div class="row between small"><span>${esc(t.class)}</span><span class="num ${i ? "muted" : ""}" style="font-weight:${i ? 500 : 650}">${fmt.pct(t.probability, 2)}</span></div><div class="track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(t.probability * 100)}" aria-label="${esc(t.class)} probability"><div class="fill" style="width:${(t.probability * 100).toFixed(2)}%"></div></div></div>`).join("")}</div>`;
  const MS = { QUEUED: ["outline", "clock"], TRAINING: ["info", "activity"], COMPLETED: ["ok", "check"], FAILED: ["bad", "x_circle"], ARCHIVED: ["", "archive"] };
  ui.modelStatus = (s, prod) => (prod ? `<span class="badge prod">${I("rocket")}Production</span> ` : "") + ui.badge(s, (MS[s] || [""])[0], (MS[s] || [])[1]);
  const JS = { queued: ["Queued", "outline", "clock"], running: ["Running", "info", "activity"], success: ["Success", "ok", "check"], partial: ["Partial", "warn", "alert"], failed: ["Failed", "bad", "x_circle"], cancelled: ["Cancelled", "", "stop"], unavailable: ["Unavailable", "bad", "wifi_off"], idle: ["Not collected", "outline", "clock"] };
  ui.jobStatus = (s) => { const r = JS[s] || [s, ""]; return ui.badge(r[0], r[1], r[2]); };
  const RS = { none: ["Not reviewed", "outline"], pending: ["Pending review", "warn", "clock"], accepted: ["Accepted", "ok", "check"], corrected: ["Corrected", "info", "edit"], rejected: ["Rejected", "bad", "x"], uncertain: ["Uncertain", "", "help"] };
  ui.reviewStatus = (s) => { const r = RS[s] || RS.none; return ui.badge(r[0], r[1], r[2]); };
  ui.thumb = (url, alt = "", cls = "thumb") => (url ? `<img class="${cls}" src="${esc(url)}" alt="${esc(alt)}" loading="lazy" decoding="async">` : `<span class="${cls} thumb-na" title="No image">${I("image")}</span>`);
  ui.warnings = (ws) => (ws && ws.length ? `<div class="health">${ws.map((w) => `<div class="health-item ${w.level === "block" ? "bad" : w.level === "warn" ? "warn" : ""}">${I(w.level === "block" ? "x_circle" : w.level === "warn" ? "alert" : "info")}<span>${esc(w.text)}</span></div>`).join("")}</div>` : `<div class="health-item ok">${I("check_circle")}<span>No issues found by the last inspection.</span></div>`);
  ui.hbars = (items, total) => { const max = Math.max(...items.map((i) => i.count), 1); return `<div class="col gap-3">${items.map((i, k) => `<div class="hbar"><span class="truncate" title="${esc(i.name)}">${esc(i.name)}</span><div class="bar" data-tip="${esc(i.name)}: ${i.count}"><span style="width:${(i.count / max) * 100}%;background:${i.color || `var(--chart-${(k % 6) + 1})`}"></span></div><span class="num muted" style="text-align:right">${total ? fmt.pct(i.count / (total || 1), 0) : fmt.n(i.count)}</span></div>`).join("")}</div>`; };
  ui.confusion = (cm, names) => {
    if (!cm || !cm.length) return ui.empty({ icon: "grid", title: "Not evaluated yet", text: "A confusion matrix appears after an evaluation run on a test split." });
    const K = names.length, rowMax = cm.map((r) => Math.max(1, lib.sum(r)));
    let h = `<div class="table-wrap"><div class="cm" style="grid-template-columns:minmax(70px,120px) repeat(${K},minmax(30px,1fr));min-width:${120 + K * 36}px" role="table" aria-label="Confusion matrix: rows are true class, columns predicted class"><div class="lab xs" style="justify-content:flex-start;align-items:flex-end">true ↓ pred →</div>`;
    names.forEach((n) => (h += `<div class="lab top" role="columnheader" title="${esc(n)}">${esc(n)}</div>`));
    cm.forEach((row, i) => { h += `<div class="lab" role="rowheader" title="${esc(names[i])}">${esc(names[i])}</div>`; row.forEach((v, j) => { const t = v / rowMax[i]; const bg = i === j ? `color-mix(in srgb, var(--accent) ${Math.round(10 + t * 75)}%, var(--surface))` : v ? `color-mix(in srgb, var(--bad) ${Math.round(12 + t * 120)}%, var(--surface))` : "var(--surface-2)"; h += `<div class="cell" role="cell" style="background:${bg}" data-tip="True ${esc(names[i])} → predicted ${esc(names[j])}: ${v} (${fmt.pct(t, 0)} of row)">${v}</div>`; }); });
    return h + `</div></div><p class="xs muted" style="margin-top:8px">Counts of test images. Rows: true class; columns: predicted class.</p>`;
  };
  ui.perClass = (pc) => `<div class="table-wrap"><table class="tbl stack"><caption class="sr-only">Per-class metrics</caption><thead><tr><th scope="col">Class</th><th scope="col" class="r">Precision</th><th scope="col" class="r">Recall</th><th scope="col" class="r">F1</th><th scope="col" class="r">Support</th></tr></thead><tbody>${(pc || []).map((p) => `<tr><td class="cell-primary" data-label="Class">${esc(p.class)}</td><td class="r num" data-label="Precision">${fmt.dec(p.precision)}</td><td class="r num" data-label="Recall">${fmt.dec(p.recall)}</td><td class="r num" data-label="F1">${fmt.dec(p.f1)}</td><td class="r num" data-label="Support">${p.support}</td></tr>`).join("")}</tbody></table></div>`;

  ui.table = ({ columns, rows, rowAct, rowData, empty, caption, cls = "stack" }) => {
    if (!rows.length) return empty || ui.empty({ icon: "search", title: "No results", text: "Nothing matches the current filters." });
    return `<div class="table-wrap"><table class="tbl ${cls}">${caption ? `<caption class="sr-only">${esc(caption)}</caption>` : ""}<thead><tr>${columns.map((c) => `<th scope="col" class="${c.cls || ""}">${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr data-key="${esc(r.id || "")}" class="${rowAct ? "click" : ""}" ${rowAct ? `data-act="${rowAct}" tabindex="0" ${dataAttrs(rowData ? rowData(r) : { id: r.id })}` : ""}>${columns.map((c, i) => `<td class="${c.cls || ""} ${i === 0 ? "cell-primary" : ""}" data-label="${esc(c.label)}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  };
  ui.pager = ({ page, per, total, act, attrs = "" }) => {
    const pages = Math.max(1, Math.ceil(total / per));
    if (total <= per) return `<div class="pager"><span>${fmt.n(total)} ${total === 1 ? "item" : "items"}</span></div>`;
    return `<nav class="pager" aria-label="Pagination"><span class="num">${fmt.n((page - 1) * per + 1)}–${fmt.n(Math.min(total, page * per))} of ${fmt.n(total)}</span><div class="row">${ui.btn({ icon: "chev_l", title: "Previous page", size: "sm", act, data: { page: page - 1 }, disabled: page <= 1 }).replace("<button", `<button ${attrs}`)}<span class="num small">Page ${page} / ${pages}</span>${ui.btn({ icon: "chev_r", title: "Next page", size: "sm", act, data: { page: page + 1 }, disabled: page >= pages }).replace("<button", `<button ${attrs}`)}</div></nav>`;
  };
  ui.timeline = (items) => `<ol class="tl">${items.map((it) => `<li><span class="tl-ic ${it.tone || ""}">${I(it.icon || "info")}</span><div class="col" style="gap:2px;min-width:0"><div class="small"><strong style="font-weight:600">${esc(it.title)}</strong>${it.note ? ` <span class="muted">${esc(it.note)}</span>` : ""}</div><div class="xs muted">${it.who ? esc(it.who) + " · " : ""}${esc(fmt.dateTime(it.at))}</div></div></li>`).join("")}</ol>`;
})(V.ui, V.lib);
