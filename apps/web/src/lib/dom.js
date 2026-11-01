/* ==========================================================================
   lib/dom.js — minimal DOM morphing so declarative re-renders keep focus,
   typed input values, scroll positions, canvases and decoded images.
   ========================================================================== */
(function (lib) {
  const tpl = document.createElement("template");
  const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute("data-key") || n.id || null : null);

  function syncAttrs(from, to) {
    const fa = from.attributes, ta = to.attributes;
    for (let i = fa.length - 1; i >= 0; i--) { const a = fa[i]; if (!to.hasAttribute(a.name)) from.removeAttribute(a.name); }
    for (let i = 0; i < ta.length; i++) { const a = ta[i]; if (from.getAttribute(a.name) !== a.value) from.setAttribute(a.name, a.value); }
    // re-apply motion-layer CSS variables (tilt, glare, spotlight) that live only on the DOM node
    if (from._fx) for (const k in from._fx) from.style.setProperty(k, from._fx[k]);
    // runtime state classes owned by imperative widgets (e.g. the 3D viewer's is-ready)
    if (from._keepClass) for (const c of from._keepClass) from.classList.add(c);
    // controlled props that must follow the declared state
    if (from.tagName === "INPUT" && (from.type === "checkbox" || from.type === "radio") && to.hasAttribute("data-controlled")) from.checked = to.hasAttribute("checked");
    if ((from.tagName === "INPUT" || from.tagName === "TEXTAREA") && to.hasAttribute("data-controlled") && document.activeElement !== from) from.value = to.getAttribute("value") ?? to.textContent;
  }

  function morphNode(from, to) {
    if (from.nodeType !== to.nodeType || from.nodeName !== to.nodeName) { from.replaceWith(to); return; }
    if (from.nodeType === 3 || from.nodeType === 8) { if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue; return; }
    if (from.nodeType !== 1) return;
    if (from.hasAttribute("data-static") && to.hasAttribute("data-static") && from.getAttribute("data-static") === to.getAttribute("data-static")) return;
    syncAttrs(from, to);
    if (from.tagName === "TEXTAREA") return; // keep typed text
    if (from.tagName === "CANVAS") return;
    morphChildren(from, to);
  }

  function morphChildren(from, to) {
    const newKids = Array.from(to.childNodes);
    let cur = from.firstChild;
    const keyed = new Map();
    for (let n = from.firstChild; n; n = n.nextSibling) { const k = keyOf(n); if (k) keyed.set(k, n); }
    for (const nk of newKids) {
      const k = keyOf(nk);
      if (k && keyed.has(k)) {
        const match = keyed.get(k); keyed.delete(k);
        if (match !== cur) from.insertBefore(match, cur);
        else cur = cur.nextSibling;
        morphNode(match, nk);
        continue;
      }
      if (cur && !keyOf(cur) && cur.nodeName === nk.nodeName) { const next = cur.nextSibling; morphNode(cur, nk); cur = next; continue; }
      from.insertBefore(nk, cur);
    }
    while (cur) { const next = cur.nextSibling; from.removeChild(cur); cur = next; }
  }

  /** Patch `el`'s children to match the given HTML string */
  lib.morph = (el, html) => {
    tpl.innerHTML = html;
    morphChildren(el, tpl.content);
  };
})(V.lib);
