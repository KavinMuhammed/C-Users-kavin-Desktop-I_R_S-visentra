/* ==========================================================================
   lib/status.js — the single source of truth for status vocabulary
   Every status string the UI shows is defined here (label, tone, icon, meaning).
   Components never hard-code status strings; they call S.meta(kind, value).
   ========================================================================== */
(function (lib) {
  const T = (label, tone, icon, text = "") => ({ label, tone, icon, text });

  const ST = {
    /* project lifecycle (backend: /api/projects/:id/status → state) */
    project: {
      CREATED: T("Created", "outline", "folder", "Project exists; no data yet."),
      DATA_COLLECTION: T("Data collection", "info", "download", "Images are being gathered and labeled."),
      DATA_VALIDATION: T("Data validation", "info", "shield", "Data needs to be validated and frozen into a dataset version."),
      DATA_READY: T("Ready for training", "ok", "check_circle", "A dataset version passed the readiness checks."),
      TRAINING: T("Training", "info", "activity", "A training job is running."),
      EVALUATION: T("Evaluation", "info", "target", "A model is being evaluated on held-out data."),
      MODEL_CANDIDATE: T("Model candidate", "violet", "cube", "A trained model is waiting for review and promotion."),
      PRODUCTION: T("Production", "ok", "rocket", "A verified model serves predictions."),
      MONITORING: T("Monitoring", "ok", "chart", "Production model with recorded predictions."),
    },
    /* blocking / failure states (backend: status → blocked) */
    blocked: {
      DATA_INSUFFICIENT: T("Data insufficient", "warn", "alert", "Too few examples for reliable training."),
      DATA_INVALID: T("Data invalid", "bad", "x_circle", "Training is impossible with the current data."),
      IMPORT_FAILED: T("Import failed", "bad", "x_circle", "The last data import failed."),
      TRAINING_FAILED: T("Training failed", "bad", "x_circle", "The last training run failed."),
      EVALUATION_FAILED: T("Evaluation failed", "bad", "x_circle", "The last evaluation failed."),
      MODEL_UNAVAILABLE: T("Model unavailable", "bad", "alert", "The production model artifact is missing."),
      DEVICE_OFFLINE: T("Device offline", "bad", "wifi_off", "A device stopped reporting."),
    },
    /* dataset readiness (backend: readiness engine) */
    readiness: {
      READY: T("Ready", "ok", "check_circle", "All readiness checks pass."),
      CONDITIONALLY_READY: T("Conditionally ready", "warn", "alert", "Training allowed; review the warnings."),
      INSUFFICIENT: T("Insufficient", "warn", "alert", "Reliable training is blocked."),
      INVALID: T("Invalid", "bad", "x_circle", "Training is not possible."),
      UNAVAILABLE: T("Analysis unavailable", "outline", "help", "Readiness could not be evaluated."),
    },
    check: { pass: T("Pass", "ok", "check"), warn: T("Warning", "warn", "alert"), insufficient: T("Insufficient", "warn", "alert"), invalid: T("Blocking", "bad", "x_circle") },
    /* model registry (derived from backend status + evaluation + production flag) */
    model: {
      TRAINING: T("Training", "info", "activity"), EVALUATING: T("Evaluating", "info", "target"),
      CANDIDATE: T("Candidate", "violet", "cube", "Trained; not evaluated on a test split."),
      VALIDATED: T("Validated", "ok", "check_circle", "Evaluated on held-out data."),
      PRODUCTION: T("Production", "prod", "rocket", "Serving predictions."),
      APPROVED: T("Approved", "ok", "shield", "Previously approved for production; eligible as a rollback target."),
      RETIRED: T("Retired", "outline", "archive"), FAILED: T("Failed", "bad", "x_circle"), QUEUED: T("Queued", "outline", "clock"),
    },
    /* review queue (backend values → product vocabulary) */
    review: {
      pending: T("Pending", "warn", "clock"), in_review: T("In review", "info", "eye"), escalated: T("Escalated", "violet", "flag"), accepted: T("Approved", "ok", "check"), corrected: T("Corrected", "info", "edit"),
      rejected: T("Rejected", "bad", "x"), uncertain: T("Escalated", "violet", "flag"), none: T("Not reviewed", "outline", "help"),
    },
    decision: { AUTO_ACCEPT: T("Auto-accepted", "ok", "check_circle", "Confidence at or above the project threshold."), REVIEW_REQUIRED: T("Review required", "warn", "flag", "Confidence below the project threshold.") },
    health: { ok: T("Healthy", "ok", "check_circle"), degraded: T("Degraded", "warn", "alert"), unavailable: T("Unavailable", "bad", "x_circle"), error: T("Error", "bad", "x_circle"), unknown: T("Unknown", "outline", "help") },
    adapter: { AVAILABLE: T("Available", "ok", "check_circle"), UNAVAILABLE: T("Unavailable", "outline", "help"), NOT_CONFIGURED: T("Not configured", "warn", "alert") },
    job: {
      queued: T("Queued", "outline", "clock"), running: T("Running", "info", "activity"), success: T("Completed", "ok", "check"),
      partial: T("Partial", "warn", "alert"), failed: T("Failed", "bad", "x_circle"), cancelled: T("Cancelled", "outline", "stop"), unavailable: T("Unavailable", "outline", "help"),
    },
    device: {
      DISCONNECTED: T("Disconnected", "outline", "wifi_off"), CONNECTING: T("Connecting", "info", "activity"), CONNECTED: T("Connected", "ok", "check_circle"),
      DEGRADED: T("Degraded", "warn", "alert"), UPDATING: T("Updating", "info", "download"), OFFLINE: T("Offline", "bad", "wifi_off"),
      NOT_CONNECTED: T("Service not connected", "outline", "help"), REVOKED: T("Revoked", "bad", "lock"),
    },
    source: {
      idle: T("Ready", "outline", "clock"), running: T("Importing", "info", "activity"), success: T("Completed", "ok", "check"), partial: T("Partial", "warn", "alert"),
      failed: T("Failed", "bad", "x_circle"), unavailable: T("Unavailable", "outline", "help"),
    },
  };
  lib.status = {
    ST,
    meta: (kind, v) => (ST[kind] && ST[kind][v]) || T(String(v || "—").replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()), "outline", "help"),
    /** Registry status of a model record from the API. */
    modelStatus(m, prodId) {
      if (!m) return "QUEUED";
      if (m.id === prodId || m.is_production) return "PRODUCTION";
      if (m.status === "ARCHIVED") return "RETIRED";
      if (m.status === "FAILED") return "FAILED";
      if (m.status === "QUEUED" || m.status === "TRAINING") return "TRAINING";
      if (m.status === "COMPLETED") return m.evaluation || m.test_metrics || (m.metrics && m.metrics.accuracy != null) ? "VALIDATED" : "CANDIDATE";
      return m.status;
    },
    /** The product state machine: which actions are valid in a lifecycle state. */
    actionsFor(state, blocked) {
      const A = {
        CREATED: ["add_data", "add_source", "settings"],
        DATA_COLLECTION: ["add_data", "add_source", "label", "validate"],
        DATA_VALIDATION: ["validate", "version", "add_data", "label"],
        DATA_READY: ["train", "validate", "add_data"],
        TRAINING: ["watch_training", "cancel_training"],
        EVALUATION: ["watch_eval"],
        MODEL_CANDIDATE: ["evaluate", "compare", "promote", "train"],
        PRODUCTION: ["predict", "review", "analytics", "devices", "train"],
        MONITORING: ["predict", "review", "analytics", "devices", "train", "compare"],
      }[state] || [];
      return blocked === "DATA_INVALID" || blocked === "DATA_INSUFFICIENT" ? A.filter((a) => a !== "train") : A;
    },
    /** Ordered lifecycle used by the intelligence map. */
    LIFECYCLE: ["CREATED", "DATA_COLLECTION", "DATA_VALIDATION", "DATA_READY", "TRAINING", "EVALUATION", "MODEL_CANDIDATE", "PRODUCTION", "MONITORING"],
    /** The core Visiontra loop. */
    LOOP: ["Observe", "Collect", "Validate", "Understand", "Train", "Evaluate", "Explain", "Deploy", "Predict", "Review", "Monitor", "Learn", "Improve"],
  };
})(V.lib);
