/* ==========================================================================
   features/aero_viewer.js — inspection image viewer.
   Shows the saved image with the detector's boxes (solid) and engineer-marked
   findings (dashed). Zoom (wheel, pinch, +/−), pan (drag, arrows), fit (0),
   select a box (click / J K), and mark a missed defect by dragging (M).
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon, A = S.aero;
  const AV = (F.aeroViewer = {});
  const vs = (id) => lib.viewState("av:" + id, { z: 1, px: 0, py: 0, sel: null, draw: false, labels: false, minScore: 0, hide: {}, mode: "annotated" });
  AV.state = vs;

  /** items: [{ id, kind: "prediction"|"human_finding", cls, score, box, decision }] */
  AV.render = (insp, items) => {
    const v = vs(insp.id), m = (insp.media || [])[0], src = m && m.urls && (m.urls.display || m.urls.original);
    const shown = items.filter((f) => !f.hidden && !v.hide[f.cls] && (f.score == null || f.score >= v.minScore));
    const n = (f) => items.indexOf(f) + 1;
    const boxes = v.mode === "original" ? "" : shown.map((f) => {
      const d = A.defect(f.cls), b = f.box;
      const lbl = `${n(f)} ${d.label}${f.score != null ? " " + Math.round(f.score * 100) + "%" : ""}`;
      return `<button type="button" class="av-box ${f.kind === "human_finding" ? "human" : "ai"} dec-${f.decision} ${v.sel === f.id ? "sel" : ""}" data-key="avb-${esc(f.id)}"
        style="left:${(b.x * 100).toFixed(3)}%;top:${(b.y * 100).toFixed(3)}%;width:${(b.w * 100).toFixed(3)}%;height:${(b.h * 100).toFixed(3)}%;--c:${d.hex}"
        data-act="av-select" data-id="${esc(insp.id)}" data-f="${esc(f.id)}" aria-pressed="${v.sel === f.id}"
        aria-label="Detection ${n(f)}: ${esc(d.label)}${f.score != null ? `, model score ${Math.round(f.score * 100)} percent` : ", marked by an engineer"}, ${esc(A.DECISIONS[f.decision][0])}">${v.labels || v.sel === f.id ? `<span class="av-lbl">${esc(lbl)}</span>` : `<span class="av-num">${n(f)}</span>`}</button>`;
    }).join("");
    const present = A.DEFECTS.filter((d) => items.some((f) => f.cls === d.id));
    const ai = items.some((f) => f.score != null);
    return `<div class="av" data-av="${esc(insp.id)}">
      <div class="av-bar" role="toolbar" aria-label="Image controls">
        ${ui.seg([["annotated", "Detections", "target"], ["original", "Original", "image"]], v.mode, "av-mode", "View", `data-id="${esc(insp.id)}"`)}
        <span class="row" style="gap:2px">${ui.iconBtn("zoom_out", "Zoom out (−)", "av-zoom", { id: insp.id, d: "-1" }, { size: "sm" })}<span class="num xs av-z" data-av-z aria-live="polite">${Math.round(v.z * 100)}%</span>${ui.iconBtn("zoom_in", "Zoom in (+)", "av-zoom", { id: insp.id, d: "1" }, { size: "sm" })}${ui.iconBtn("fit", "Fit to view (0)", "av-fit", { id: insp.id }, { size: "sm" })}${ui.iconBtn("reset", "Reset zoom, filters and labels", "av-reset", { id: insp.id }, { size: "sm" })}</span>
        <span class="grow"></span>
        ${present.map((d) => `<button type="button" class="av-chip" style="--c:${d.hex}" data-act="av-cat" data-id="${esc(insp.id)}" data-c="${d.id}" aria-pressed="${!v.hide[d.id]}"><span class="sw" aria-hidden="true"></span>${esc(d.label)} <span class="num">${items.filter((f) => f.cls === d.id).length}</span></button>`).join("")}
        ${ai ? `<label class="av-range xs" title="Hide detections below this confidence"><span>Min.</span><input type="range" min="0" max="90" step="5" value="${Math.round(v.minScore * 100)}" data-act="av-min" data-on="input" data-id="${esc(insp.id)}" aria-label="Minimum confidence shown"><span class="num">${Math.round(v.minScore * 100)}%</span></label>` : ""}
        ${ui.iconBtn("tag", v.labels ? "Show numbers only on the image" : "Show class names on the image", "av-labels", { id: insp.id }, { size: "sm" })}
        ${AV.canMark(insp) ? ui.btn({ label: v.draw ? "Drag on the image… (Esc)" : "Mark missed defect", icon: "plus", size: "sm", variant: v.draw ? "primary" : "", act: "av-draw", data: { id: insp.id }, title: "Draw a box around a defect the model missed (M)" }) : ""}
      </div>
      <div class="av-stage ${v.draw ? "drawing" : ""}" data-av-stage="${esc(insp.id)}" tabindex="0" role="group" aria-roledescription="image viewer"
        aria-label="Inspection image ${esc(insp.ref)}. Plus and minus zoom, arrow keys pan, 0 fits, J and K step through detections${AV.canMark(insp) ? ", M marks a missed defect" : ""}.">
        <div class="av-move" style="transform:translate(${v.px}px,${v.py}px) scale(${v.z})"><div class="av-frame">
          ${src ? `<img src="${esc(src)}" alt="Aircraft surface image ${esc(insp.ref)}" draggable="false" decoding="async">` : `<div class="av-noimg">${I("image", "lg")}<span>Image not available</span></div>`}
          <div class="av-ov">${boxes}</div><div class="av-draft" hidden></div>
        </div></div>
        ${v.mode !== "original" && !items.length ? (() => { const st = (insp.detection || {}).state;
          return st === "completed_zero" ? `<div class="av-note">${I("info")} Detection ran: no candidates at the configured thresholds — not proof the surface is defect-free. Examine the image yourself.</div>`
            : st === "relevance_rejected" ? `<div class="av-note bad">${I("alert")} Not analysed: the aircraft-surface check rejected this image. No AI results exist for it.</div>`
            : st === "failed" ? `<div class="av-note bad">${I("alert")} Detection failed — no AI results for this image.</div>`
            : st === "not_analysed" ? `<div class="av-note">${I("info")} Not analysed by the detection model.</div>` : ""; })() : ""}
      </div>
    </div>`;
  };
  AV.canMark = (insp) => insp.status !== "reviewed" && insp.status !== "analysing" && insp.status !== "analysis_failed" && (!insp.review_permissions || insp.review_permissions.review);

  const root = (id) => document.querySelector(`.av[data-av="${CSS.escape(id)}"]`);
  function apply(id) {
    const v = vs(id), r = root(id); if (!r) return;
    const m = r.querySelector(".av-move"); if (m) m.style.transform = `translate(${v.px}px,${v.py}px) scale(${v.z})`;
    const z = r.querySelector("[data-av-z]"); if (z) z.textContent = Math.round(v.z * 100) + "%";
    r.style.setProperty("--z", v.z);
  }
  function zoomAt(id, f, cx, cy) {
    const v = vs(id), r = root(id), st = r && r.querySelector(".av-stage"); if (!st) return;
    const rc = st.getBoundingClientRect(), ox = rc.left + rc.width / 2, oy = rc.top + rc.height / 2;
    const sx = cx == null ? ox : cx, sy = cy == null ? oy : cy, nz = lib.clamp(v.z * f, 0.5, 12);
    if (nz === v.z) return;
    const lx = (sx - ox - v.px) / v.z, ly = (sy - oy - v.py) / v.z;
    v.px = sx - ox - nz * lx; v.py = sy - oy - nz * ly; v.z = nz; apply(id);
  }
  const fit = (id) => { Object.assign(vs(id), { z: 1, px: 0, py: 0 }); apply(id); };
  AV.focus = (id, fid) => { vs(id).sel = fid; lib.store.emit(); };

  lib.on("av-mode", (el) => { const v = vs(el.dataset.id); v.mode = el.dataset.v; if (v.mode === "original") v.draw = false; lib.store.emit(); });
  lib.on("av-zoom", (el) => zoomAt(el.dataset.id, el.dataset.d === "1" ? 1.25 : 0.8));
  lib.on("av-fit", (el) => fit(el.dataset.id));
  lib.on("av-reset", (el) => { Object.assign(vs(el.dataset.id), { z: 1, px: 0, py: 0, hide: {}, minScore: 0, labels: false, draw: false }); lib.store.emit(); requestAnimationFrame(() => apply(el.dataset.id)); });
  lib.on("av-labels", (el) => { const v = vs(el.dataset.id); v.labels = !v.labels; lib.store.emit(); });
  lib.on("av-cat", (el) => { const v = vs(el.dataset.id); v.hide[el.dataset.c] = !v.hide[el.dataset.c]; lib.store.emit(); });
  lib.on("av-min", (el) => { vs(el.dataset.id).minScore = +el.value / 100; lib.store.emit(); });
  lib.on("av-draw", (el) => { const v = vs(el.dataset.id); v.draw = !v.draw; v.mode = "annotated"; lib.store.emit(); });
  lib.on("av-select", (el) => { const st = el.closest(".av-stage"); if (st && st._dragged) return; const v = vs(el.dataset.id); v.sel = v.sel === el.dataset.f ? null : el.dataset.f; lib.store.emit();
    requestAnimationFrame(() => { const li = document.querySelector(`[data-det="${CSS.escape(el.dataset.f)}"]`); li && li.scrollIntoView({ block: "nearest" }); }); });

  /** Pointer and keyboard handling for every viewer on the page (idempotent). onBox(id, box) is called after a drawn box. */
  AV.mount = (onBox) => {
    document.querySelectorAll(".av-stage").forEach((stage) => {
      stage._onBox = onBox;
      if (stage._av) return; stage._av = true;
      const id = stage.dataset.avStage, pts = new Map();
      let drag = null;
      stage.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(id, e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
      const frac = (e) => { const fr = stage.querySelector(".av-frame").getBoundingClientRect(); return { x: lib.clamp((e.clientX - fr.left) / fr.width, 0, 1), y: lib.clamp((e.clientY - fr.top) / fr.height, 0, 1) }; };
      stage.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const v = vs(id);
        if (pts.size === 2) { const [a, b] = [...pts.values()]; drag = { kind: "pinch", d0: Math.hypot(a.x - b.x, a.y - b.y), z0: v.z }; const dr = stage.querySelector(".av-draft"); if (dr) dr.hidden = true; return; }
        if (v.draw && !e.target.closest(".av-box")) { const p = frac(e); drag = { kind: "draw", p0: p, box: null }; stage.setPointerCapture(e.pointerId); e.preventDefault(); return; }
        drag = { kind: "pan", x: e.clientX, y: e.clientY, px: v.px, py: v.py, moved: false };
      });
      stage.addEventListener("pointermove", (e) => {
        if (!drag) return;
        if (pts.has(e.pointerId)) pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const v = vs(id);
        if (drag.kind === "pinch" && pts.size >= 2) { const [a, b] = [...pts.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); v.z = lib.clamp(drag.z0 * d / (drag.d0 || 1), 0.5, 12); apply(id); return; }
        if (drag.kind === "draw") {
          const p = frac(e), x = Math.min(p.x, drag.p0.x), y = Math.min(p.y, drag.p0.y);
          drag.box = { x, y, w: Math.abs(p.x - drag.p0.x), h: Math.abs(p.y - drag.p0.y) };
          const dr = stage.querySelector(".av-draft"); if (dr) { dr.hidden = false; Object.assign(dr.style, { left: x * 100 + "%", top: y * 100 + "%", width: drag.box.w * 100 + "%", height: drag.box.h * 100 + "%" }); }
          return;
        }
        if (drag.kind === "pan") {
          const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
          if (!drag.moved && Math.hypot(dx, dy) < 4) return;
          if (!drag.moved) { drag.moved = true; try { stage.setPointerCapture(e.pointerId); } catch {} }
          v.px = drag.px + dx; v.py = drag.py + dy; stage._dragged = true; apply(id);
        }
      });
      const end = (e) => {
        pts.delete(e.pointerId);
        const d = drag; drag = null; if (!d) return;
        setTimeout(() => { stage._dragged = false; }, 0);
        if (d.kind === "draw") {
          const dr = stage.querySelector(".av-draft"); if (dr) dr.hidden = true;
          if (d.box && d.box.w > 0.006 && d.box.h > 0.006) { if (stage._onBox) stage._onBox(id, d.box); }
          else if (d.box) ui.toast("That box is too small. Drag a rectangle around the defect.", "warn");
        }
      };
      stage.addEventListener("pointerup", end); stage.addEventListener("pointercancel", end);
      stage.addEventListener("keydown", (e) => {
        const v = vs(id), step = 40;
        if (e.key === "+" || e.key === "=") zoomAt(id, 1.25);
        else if (e.key === "-" || e.key === "_") zoomAt(id, 0.8);
        else if (e.key === "0") fit(id);
        else if (e.key === "ArrowLeft") { v.px += step; apply(id); }
        else if (e.key === "ArrowRight") { v.px -= step; apply(id); }
        else if (e.key === "ArrowUp") { v.py += step; apply(id); }
        else if (e.key === "ArrowDown") { v.py -= step; apply(id); }
        else if ((e.key === "m" || e.key === "M") && stage.closest(".av").querySelector('[data-act="av-draw"]')) { v.draw = !v.draw; v.mode = "annotated"; lib.store.emit(); }
        else if (e.key === "Escape" && v.draw) { v.draw = false; lib.store.emit(); }
        else return;
        e.preventDefault(); e.stopPropagation();
      });
    });
  };
})(V.features, V.ui, V.lib, V.services);
