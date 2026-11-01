/* ==========================================================================
   services/api.js — the only place that talks to the network.
   Typed (JSDoc) service objects over the Visiontra REST API.
   ========================================================================== */
/**
 * @typedef {{code:string,message:string,field?:string,request_id?:string,status:number}} ApiError
 * @typedef {{id:string,name:string,index:number}} ProjectClass
 * @typedef {{id:string,name:string,classes:ProjectClass[],confidence_threshold:number,production_model_id:?string,counts?:Object}} Project
 * @typedef {{class:string,probability:number}} TopK
 * @typedef {{id:string,predicted_class:string,confidence:number,topk:TopK[],model_version:number,latency_ms:number,low_confidence:boolean,explanation_status:string,explanation_url:?string}} Prediction
 */
(function (S, lib) {
  let csrf = null;
  S.setCsrf = (t) => { csrf = t; };
  const cookieCsrf = () => { const m = document.cookie.match(/(?:^|; )visentra_csrf=([^;]+)/); return m ? decodeURIComponent(m[1]) : null; };

  const idemKey = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  /** extra: optional headers (e.g. `If-Match` for Phase 5 inspection mutations); auth/CSRF headers are always set here. */
  async function request(method, path, { json, form, raw, idempotent, headers: extra } = {}) {
    const headers = {};
    if (extra) for (const [k, v] of Object.entries(extra)) if (v != null && !/^(x-csrf-token|cookie|authorization)$/i.test(k)) headers[k] = String(v);
    if (idempotent) headers["Idempotency-Key"] = typeof idempotent === "string" ? idempotent : idemKey();
    if (method !== "GET") headers["X-CSRF-Token"] = csrf || cookieCsrf() || "";
    let body;
    if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
    if (form) body = form;
    let res;
    // never wait forever: a hung request becomes a TIMEOUT error the page can show with a retry
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), method === "GET" ? 30000 : 180000) : null;
    try { res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: ctl ? ctl.signal : undefined }); }
    catch (e) {
      if (e && e.name === "AbortError") throw Object.assign(new Error("The server did not answer in time. Check that it is running, then try again."), { code: "TIMEOUT", status: 0 });
      throw Object.assign(new Error("Can't reach the VisionTra server. Check that it is running and your connection is up."), { code: "NETWORK", status: 0 });
    }
    finally { if (timer) clearTimeout(timer); }
    if (raw && res.ok) return res;
    const ctype = res.headers.get("content-type") || "";
    const data = ctype.includes("application/json") ? await res.json().catch(() => ({})) : {};
    if (!res.ok) {
      const e = data.error || {};
      throw Object.assign(new Error(e.message || `Request failed (HTTP ${res.status}).`), { code: e.code || (res.status === 401 ? "UNAUTHENTICATED" : res.status === 403 ? "FORBIDDEN" : res.status === 404 ? "NOT_FOUND" : res.status === 429 ? "RATE_LIMITED" : "SERVER"), field: e.field, details: e.details || {}, status: res.status, request_id: e.request_id || res.headers.get("X-Request-ID") });
    }
    return data;
  }
  const qs = (o = {}) => { const p = Object.entries(o).filter(([, v]) => v != null && v !== ""); return p.length ? "?" + new URLSearchParams(p).toString() : ""; };
  const get = (p, q) => request("GET", p + qs(q));
  const post = (p, json) => request("POST", p, { json: json || {} });
  S.request = request;

  S.auth = {
    me: () => get("/api/auth/me"),
    login: (email, password) => post("/api/auth/login", { email, password }),
    register: (name, email, password) => post("/api/auth/register", { name, email, password }),
    logout: () => post("/api/auth/logout"),
    forgot: (email) => post("/api/auth/forgot", { email }),
    reset: (token, password) => post("/api/auth/reset", { token, password }),
    updateProfile: (name) => request("PATCH", "/api/auth/me", { json: { name } }),
    changePassword: (current, next) => post("/api/auth/me/password", { current, new: next }),
    savePreferences: (prefs) => request("PUT", "/api/auth/me/preferences", { json: prefs }),
  };
  S.projects = {
    /** @returns {Promise<Project[]>} */ list: (q) => get("/api/projects", q),
    /** @returns {Promise<Project>} */ get: (id) => get(`/api/projects/${id}`),
    create: (data) => post("/api/projects", data),
    update: (id, data) => request("PATCH", `/api/projects/${id}`, { json: data }),
    archive: (id) => post(`/api/projects/${id}/archive`),
    addClass: (id, name) => post(`/api/projects/${id}/classes`, { name }),
    renameClass: (id, cid, name) => request("PATCH", `/api/projects/${id}/classes/${cid}`, { json: { name } }),
    deleteClass: (id, cid) => request("DELETE", `/api/projects/${id}/classes/${cid}`),
    checklist: (id) => get(`/api/projects/${id}/checklist`),
    members: (id) => get(`/api/projects/${id}/members`),
    addMember: (id, email, role) => post(`/api/projects/${id}/members`, { email, role }),
    removeMember: (id, uid) => request("DELETE", `/api/projects/${id}/members/${uid}`),
  };
  S.data = {
    upload: (pid, files, classId) => { const f = new FormData(); files.forEach((x) => f.append("files", x, x.name)); if (classId) f.append("class_id", classId); return request("POST", `/api/projects/${pid}/uploads`, { form: f }); },
    /** Upload with byte-level progress (XHR), onProgress(fraction). */
    uploadWithProgress: (pid, files, classId, onProgress) => new Promise((resolve, reject) => {
      const f = new FormData(); files.forEach((x) => f.append("files", x, x.name)); if (classId) f.append("class_id", classId);
      const x = new XMLHttpRequest();
      x.open("POST", `/api/projects/${pid}/uploads`);
      x.setRequestHeader("X-CSRF-Token", csrf || cookieCsrf() || "");
      x.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
      x.onerror = () => reject(Object.assign(new Error("Upload failed: the server could not be reached."), { code: "NETWORK" }));
      x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch {} if (x.status >= 200 && x.status < 300) resolve(d); else { const e = d.error || {}; reject(Object.assign(new Error(e.message || `Upload failed (HTTP ${x.status}).`), { code: e.code || (x.status === 401 ? "UNAUTHENTICATED" : "SERVER"), status: x.status })); } };
      x.send(f);
    }),
    importZip: (pid, file, meta) => { const f = new FormData(); f.append("file", file, file.name); Object.entries(meta).forEach(([k, v]) => f.append(k, v || "")); return request("POST", `/api/projects/${pid}/imports/zip`, { form: f }); },
    images: (pid, q) => get(`/api/projects/${pid}/images`, q),
    image: (id) => get(`/api/images/${id}`),
    label: (id, classId) => request("PUT", `/api/images/${id}/label`, { json: { class_id: classId || null } }),
    bulkLabel: (pid, ids, classId) => post(`/api/projects/${pid}/images/bulk-label`, { image_ids: ids, class_id: classId || null }),
    deleteImages: (pid, ids) => post(`/api/projects/${pid}/images/delete`, { image_ids: ids }),
    quality: (pid) => get(`/api/projects/${pid}/quality`),
    versions: (pid) => get(`/api/projects/${pid}/dataset-versions`),
    createVersion: (pid, data) => post(`/api/projects/${pid}/dataset-versions`, data),
  };
  S.sources = {
    list: (pid) => get("/api/data-sources", { project_id: pid }),
    get: (id) => get(`/api/data-sources/${id}`),
    allowlist: () => get("/api/data-sources/allowlist"),
    create: (data) => post("/api/data-sources", data),
    collect: (id) => post(`/api/data-sources/${id}/collect`),
    remove: (id) => request("DELETE", `/api/data-sources/${id}`),
  };
  S.jobs = {
    list: (q) => get("/api/jobs", q),
    get: (id) => get(`/api/jobs/${id}`),
    cancel: (id) => post(`/api/jobs/${id}/cancel`),
  };
  S.training = {
    hardware: () => get("/api/system/hardware"),
    architectures: () => get("/api/architectures"),
    /** acknowledgeInsufficient: explicit, audited override when the version's readiness is INSUFFICIENT. */
    start: (projectId, datasetVersionId, config, acknowledgeInsufficient = false) => request("POST", "/api/training-runs", { json: { project_id: projectId, dataset_version_id: datasetVersionId, config, acknowledge_insufficient: !!acknowledgeInsufficient }, idempotent: true }),
    runs: (pid) => get("/api/training-runs", { project_id: pid }),
    run: (id) => get(`/api/training-runs/${id}`),
    logs: (id, after) => get(`/api/training-runs/${id}/logs`, { after }),
    metrics: (id) => get(`/api/training-runs/${id}/metrics`),
    cancel: (id) => post(`/api/training-runs/${id}/cancel`),
    job: (id) => get(`/api/training/jobs/${id}`),
    experiments: (pid) => get("/api/experiments", { project_id: pid }),
    experiment: (id) => get(`/api/experiments/${id}`),
  };
  S.models = {
    list: (pid) => get("/api/models", { project_id: pid }),
    get: (id) => get(`/api/models/${id}`),
    compare: (ids) => get("/api/models/compare", { ids: ids.join(",") }),
    promote: (id, reason = "") => request("POST", `/api/models/${id}/promote`, { json: { reason }, idempotent: true }),
    demote: (id) => post(`/api/models/${id}/demote`),
    archive: (id) => post(`/api/models/${id}/retire`),
    rollback: (pid, reason, modelId) => post(`/api/projects/${pid}/production/rollback`, { reason, model_version_id: modelId || null }),
    history: (pid) => get(`/api/projects/${pid}/production/history`),
    lineage: (id) => get(`/api/models/${id}/lineage`),
    evaluate: (id, dvId) => post(`/api/models/${id}/evaluate`, { dataset_version_id: dvId }),
    downloadUrl: (id) => `/api/models/${id}/download`,
  };
  S.inference = {
    /** @returns {Promise<Prediction>} */
    classify: (pid, file, modelId, explain = true) => { const f = new FormData(); f.append("file", file, file.name || "pasted.png"); f.append("project_id", pid); if (modelId) f.append("model_id", modelId); f.append("explain", explain ? "true" : "false"); return request("POST", "/api/inference", { form: f }); },
    get: (id) => get(`/api/inference/${id}`),
    list: (q) => get("/api/inference", q),
    explanation: (id) => get(`/api/predictions/${id}/explanation`),
    explain: (id) => post(`/api/predictions/${id}/explanation`),
    lineage: (id) => get(`/api/predictions/${id}/lineage`),
    batch: (pid, files, explain = false) => { const f = new FormData(); files.forEach((x) => f.append("files", x, x.name)); f.append("explain", explain ? "true" : "false"); return request("POST", `/api/projects/${pid}/batch-inference`, { form: f }); },
    batchStatus: (jobId) => get(`/api/batch-inference/${jobId}`),
    requestReview: (id, note) => post(`/api/inference/${id}/review`, { note }),
    exportUrl: (pid) => `/api/inference-export.csv?project_id=${encodeURIComponent(pid)}`,
  };
  S.reviews = {
    list: (q) => get("/api/reviews", q),
    decide: (id, action, data = {}) => post(`/api/reviews/${id}/${action}`, data),
    claim: (id) => post(`/api/reviews/${id}/claim`),
    release: (id) => post(`/api/reviews/${id}/release`),
    history: (id) => get(`/api/reviews/${id}/history`),
    feedback: (pid) => get(`/api/projects/${pid}/feedback`),
    activeLearning: (pid) => post(`/api/projects/${pid}/active-learning`),
  };
  S.analytics = { get: (q) => get("/api/analytics", q) };
  /**
   * Project intelligence (DatasetAnalysisService + LineageService + lifecycle).
   * @typedef {{code:string,severity:'pass'|'warn'|'insufficient'|'invalid',title:string,detail:string,class_name?:string,value?:number}} ReadinessCheck
   * @typedef {{status:'READY'|'CONDITIONALLY_READY'|'INSUFFICIENT'|'INVALID',summary:string,checks:ReadinessCheck[],recommendations:Array,class_counts:Array,thresholds:Object}} Readiness
   * @typedef {{state:string,blocked:?string,next_action:?{key:string,label:string,text:string,to:string},readiness:Object,dataset_version:?Object,production_model:?Object}} ProjectStatus
   */
  S.intel = {
    /** @returns {Promise<Readiness>} */ readiness: (pid) => get(`/api/projects/${pid}/readiness`),
    /** @returns {Promise<Readiness>} */ versionReadiness: (vid) => get(`/api/dataset-versions/${vid}/readiness`),
    /** @returns {Promise<ProjectStatus>} */ status: (pid) => get(`/api/projects/${pid}/status`),
    lineage: (pid) => get(`/api/projects/${pid}/lineage`),
  };
  S.audit = { list: (q) => get("/api/audit-log", q), get: (id) => get(`/api/audit/${id}`), exportUrl: (pid) => "/api/audit-export.csv" + qs({ project_id: pid }) };
  S.notifications = {
    list: (q) => get("/api/notifications", q),
    read: (id) => post(`/api/notifications/${id}/read`),
    readAll: () => post("/api/notifications/read-all"),
  };
  S.workspaces = {
    list: () => get("/api/workspaces"),
    get: (id) => get(`/api/workspaces/${id}`),
    create: (name) => post("/api/workspaces", { name }),
    rename: (id, name) => request("PATCH", `/api/workspaces/${id}`, { json: { name } }),
    members: (id) => get(`/api/workspaces/${id}/members`),
    setMember: (id, email, role) => post(`/api/workspaces/${id}/members`, { email, role }),
    removeMember: (id, uid) => request("DELETE", `/api/workspaces/${id}/members/${uid}`),
    settings: (id) => get(`/api/workspaces/${id}/settings`),
    saveSettings: (id, data) => request("PATCH", `/api/workspaces/${id}/settings`, { json: data }),
  };
  /** Server-side device registry (token-authenticated agents). Status is derived from real heartbeats. */
  S.registry = {
    list: (q) => get("/api/devices", q),
    get: (id) => get(`/api/devices/${id}`),
    register: (data) => post("/api/devices", data),
    update: (id, data) => request("PATCH", `/api/devices/${id}`, { json: data }),
    rotate: (id) => post(`/api/devices/${id}/rotate-token`),
    revoke: (id) => post(`/api/devices/${id}/revoke`),
    events: (id) => get(`/api/devices/${id}/events`),
    adapters: () => get("/api/hardware/adapters"),
  };
  S.system = {
    live: () => get("/api/health/live"),
    /** 200 = ok/degraded, 503 = unavailable; both carry the measured checks. */
    ready: async () => { let r; try { r = await fetch("/api/health/ready", { credentials: "same-origin" }); } catch { throw Object.assign(new Error("Can't reach the Visiontra server."), { code: "NETWORK", status: 0 }); } return r.json(); },
    capabilities: () => get("/api/system/capabilities"),
    version: () => get("/api/system/version"),
  };
  S.search = { query: (q) => get("/api/search", { q }) };
  /**
   * Phase 8 — development-only experimental detection (docs/PHASE_8_INFERENCE_CONTRACT.md).
   * Separate from the inspection adapters on purpose: nothing here is an inspection, finding or report.
   */
  S.experimental = {
    status: () => get("/api/v1/experimental/detection/status"),
    predict: (modelId, file) => { const f = new FormData(); f.append("file", file, file.name || "image"); return request("POST", `/api/v1/experimental/models/${encodeURIComponent(modelId)}/predict`, { form: f }); },
    runs: () => get("/api/audit-log", { action: "experimental_inference.run", per_page: 8 }),
    /** Phase 10: persisted experimental runs (own runs, or the runs linked to one saved inspection). */
    savedRuns: (q = {}) => get("/api/v1/experimental/runs", q),
    savedRun: (id) => get(`/api/v1/experimental/runs/${encodeURIComponent(id)}`),
    /** Phase 10: run the experimental model on a saved inspection's stored image. Never changes the inspection. */
    runOnInspection: (inspectionId, modelId) => request("POST", `/api/v1/experimental/inspections/${encodeURIComponent(inspectionId)}/predict${modelId ? "?model_id=" + encodeURIComponent(modelId) : ""}`, { json: {} }),
    /**
     * Phase 9: same endpoint as predict(), sent with XHR so the page can tell "uploading" from
     * "server processing", with a client trace id (X-Request-ID) and measured upload/round-trip times.
     * Resolves {data, timing}; rejects with the same error shape as request().
     */
    predictTracked: (modelId, file, { requestId, onUploaded, timeoutMs = 120000 } = {}) => new Promise((resolve, reject) => {
      const f = new FormData(); f.append("file", file, file.name || "image");
      const x = new XMLHttpRequest(), t0 = performance.now(); let tUp = null;
      x.open("POST", `/api/v1/experimental/models/${encodeURIComponent(modelId)}/predict`);
      x.setRequestHeader("X-CSRF-Token", csrf || cookieCsrf() || "");
      if (requestId) x.setRequestHeader("X-Request-ID", requestId);
      x.timeout = timeoutMs;
      x.upload.onload = () => { tUp = performance.now(); onUploaded && onUploaded(); };
      const lost = (why) => reject(Object.assign(new Error(why), { code: "NETWORK", status: 0, details: {}, request_id: requestId || null }));
      x.onerror = () => lost("The VisionTra server could not be reached, or the connection dropped before an answer arrived.");
      x.ontimeout = () => lost("No answer from the server within the browser's time limit.");
      x.onload = () => {
        const t1 = performance.now();
        try { const data = S.expdet.parseXhr(x.status, x.responseText, x.getResponseHeader("X-Request-ID")); resolve({ data, timing: { upload_ms: tUp != null ? tUp - t0 : null, round_trip_ms: t1 - t0 } }); }
        catch (e) { if (e.code === "UNAUTHENTICATED") V.app.onUnauthenticated && V.app.onUnauthenticated(); reject(e); }
      };
      x.send(f);
    }),
  };
})(V.services, V.lib);
