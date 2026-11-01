/* ==========================================================================
   features/auth.js — landing / sign in / register / password reset
   ========================================================================== */
(function (F, ui, lib, S) {
  const esc = lib.esc, I = lib.icon;
  const A = F.auth = {};
  const vs = () => lib.viewState("auth", { errors: {}, formError: null, busy: false, sent: false, done: false, showPw: false });
  const reset = () => Object.assign(vs(), { errors: {}, formError: null, busy: false });
  const emailOk = (e) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e || "");

  A.logo = (size = 36) => `<span class="brand-mark" aria-hidden="true">${lib.brandMark(size)}</span>`;
  const shell = (inner) => `<div class="hero"><aside class="hero-left">
      <div class="hero-ac">${lib.ac3dView({ key: "hero", mode: "hero", zones: {}, label: "VisionTra 3D aircraft", legend: false })}
        <div class="hero-hud h1"><span>CRACK</span><span>DENT</span><span>CORROSION</span></div><div class="hero-hud h2"><span>SURFACE SCAN</span><span class="mono">AI · ENGINEER REVIEW</span></div></div>
      <div class="row" style="gap:10px;position:relative">${A.logo(44)}<div class="col" style="gap:2px"><span class="brand-name brand-name-lg">${lib.brandWordmark(19)}</span><span class="brand-sub">Aircraft surface inspection</span></div></div>
      <div class="col gap-4" style="position:relative"><h1 class="hero-title">Find cracks, dents and corrosion <em>from a photo</em>.</h1><p class="subtle" style="max-width:46ch;font-size:var(--fs-lg)">Upload aircraft surface images or drone frames. VisionTra detects and locates defects, an engineer confirms each one, and the inspection is documented in a traceable PDF report.</p>
      <div class="hero-steps"><span>Upload</span><span>·</span><span>Detect</span><span>·</span><span>Review</span><span>·</span><span>Report</span></div></div>
      <p class="xs muted" style="position:relative">Decision support for qualified engineers. Self-hosted: images and models stay on your server.</p></aside>
      <main class="hero-right" id="main"><div class="auth-card">${inner}</div></main></div>`;
  const formErr = () => (vs().formError ? ui.banner("bad", "alert_circle", esc(vs().formError)) : "");

  A.login = () => {
    const v = vs(); const expired = lib.ui.state.sessionExpired;
    return shell(`<div class="col gap-1"><h2 style="font-size:var(--fs-2xl)">Sign in</h2><p class="muted">Welcome back.</p></div>
      ${expired ? ui.banner("warn", "clock", "Your session ended. Sign in again to continue.") : ""}${formErr()}
      <form class="col gap-4" data-act="auth-login" novalidate>
        ${ui.field({ id: "login-email", label: "Email", error: v.errors.email, control: ui.input({ id: "login-email", name: "email", type: "email", autocomplete: "username", error: v.errors.email }) })}
        ${ui.field({ id: "login-password", label: "Password", error: v.errors.password, control: ui.input({ id: "login-password", name: "password", type: "password", autocomplete: "current-password", error: v.errors.password }) })}
        <div class="row end"><a href="#/forgot" data-act="nav" data-to="/forgot" class="small">Forgot password?</a></div>
        ${ui.btn({ label: "Sign in", variant: "primary", size: "lg", type: "submit", cls: "block", busy: v.busy })}
      </form><p class="small muted" style="text-align:center">New to VisionTra? <a href="#/register" data-act="nav" data-to="/register">Create an account</a></p>`);
  };
  A.register = () => {
    const v = vs();
    return shell(`<div class="col gap-1"><h2 style="font-size:var(--fs-2xl)">Create your account</h2><p class="muted">Your workspace starts empty. Upload an aircraft image to run the first inspection.</p></div>${formErr()}
      <form class="col gap-4" data-act="auth-register" novalidate>
        ${ui.field({ id: "reg-name", label: "Full name", error: v.errors.name, control: ui.input({ id: "reg-name", name: "name", autocomplete: "name", error: v.errors.name }) })}
        ${ui.field({ id: "reg-email", label: "Email", error: v.errors.email, control: ui.input({ id: "reg-email", name: "email", type: "email", autocomplete: "email", error: v.errors.email }) })}
        ${ui.field({ id: "reg-pw", label: "Password", error: v.errors.password, hint: "At least 10 characters, including a letter and a number.", control: ui.input({ id: "reg-pw", name: "password", type: "password", autocomplete: "new-password", error: v.errors.password }) })}
        ${ui.btn({ label: "Create account", variant: "primary", size: "lg", type: "submit", cls: "block", busy: v.busy })}
      </form><p class="small muted" style="text-align:center">Already registered? <a href="#/login" data-act="nav" data-to="/login">Sign in</a></p>`);
  };
  A.forgot = () => {
    const v = vs();
    if (v.sent) return shell(`<div class="col gap-3"><h2 style="font-size:var(--fs-xl)">Check with your administrator</h2><p class="muted">If an account exists for that email, a one-hour reset link was issued. This server has no mail transport configured, so the link is written to the server log for the operator to deliver.</p><a href="#/login" data-act="auth-back" class="small">← Back to sign in</a></div>`);
    return shell(`<div class="col gap-1"><h2 style="font-size:var(--fs-2xl)">Reset password</h2><p class="muted">Enter your account email.</p></div>${formErr()}
      <form class="col gap-4" data-act="auth-forgot" novalidate>${ui.field({ id: "fg-email", label: "Email", error: v.errors.email, control: ui.input({ id: "fg-email", name: "email", type: "email", autocomplete: "email", error: v.errors.email }) })}${ui.btn({ label: "Request reset link", variant: "primary", size: "lg", type: "submit", cls: "block", busy: v.busy })}</form><a href="#/login" data-act="auth-back" class="small">← Back to sign in</a>`);
  };
  A.reset = (token) => {
    const v = vs();
    if (v.done) return shell(`<div class="col gap-3"><h2 style="font-size:var(--fs-xl)">Password updated</h2><p class="muted">All other sessions were signed out.</p>${ui.btn({ label: "Sign in", variant: "primary", act: "auth-back", cls: "block" })}</div>`);
    return shell(`<div class="col gap-1"><h2 style="font-size:var(--fs-2xl)">Choose a new password</h2></div>${formErr()}
      <form class="col gap-4" data-act="auth-reset" data-token="${esc(token)}" novalidate>${ui.field({ id: "rs-pw", label: "New password", error: v.errors.password, hint: "At least 10 characters, including a letter and a number.", control: ui.input({ id: "rs-pw", name: "password", type: "password", autocomplete: "new-password", error: v.errors.password }) })}${ui.field({ id: "rs-pw2", label: "Confirm password", error: v.errors.confirm, control: ui.input({ id: "rs-pw2", name: "confirm", type: "password", autocomplete: "new-password", error: v.errors.confirm }) })}${ui.btn({ label: "Update password", variant: "primary", size: "lg", type: "submit", cls: "block", busy: v.busy })}</form>`);
  };
  A.mount = () => {};

  const fail = (e) => { const v = vs(); v.busy = false; v.formError = e.message; if (e.field) v.errors[e.field] = e.message; lib.store.emit(); };
  lib.on("auth-back", () => { Object.assign(vs(), { sent: false, done: false }); reset(); F.go("/login"); });
  lib.on("auth-login", async (form) => {
    const d = lib.formData(form), v = vs(); reset();
    if (!emailOk(d.email)) v.errors.email = "Enter a valid email address.";
    if (!d.password) v.errors.password = "Enter your password.";
    if (Object.keys(v.errors).length) return lib.store.emit();
    v.busy = true; lib.store.emit();
    try { const r = await S.auth.login(d.email, d.password); await V.app.signedIn(r); reset(); lib.ui.state.sessionExpired = false; F.go(lib.ui.state.afterLogin || "/"); lib.ui.state.afterLogin = null; }
    catch (e) { fail(e); }
  });
  lib.on("auth-register", async (form) => {
    const d = lib.formData(form), v = vs(); reset();
    if (!d.name || d.name.trim().length < 2) v.errors.name = "Enter your name.";
    if (!emailOk(d.email)) v.errors.email = "Enter a valid email address.";
    if (!d.password || d.password.length < 10) v.errors.password = "Use at least 10 characters.";
    if (Object.keys(v.errors).length) return lib.store.emit();
    v.busy = true; lib.store.emit();
    try { const r = await S.auth.register(d.name.trim(), d.email, d.password); await V.app.signedIn(r); reset(); F.go("/inspection"); }
    catch (e) { fail(e); }
  });
  lib.on("auth-forgot", async (form) => {
    const d = lib.formData(form), v = vs(); reset();
    if (!emailOk(d.email)) { v.errors.email = "Enter a valid email address."; return lib.store.emit(); }
    v.busy = true; lib.store.emit();
    try { await S.auth.forgot(d.email); Object.assign(v, { sent: true, busy: false }); lib.store.emit(); } catch (e) { fail(e); }
  });
  lib.on("auth-reset", async (form) => {
    const d = lib.formData(form), v = vs(); reset();
    if (!d.password || d.password.length < 10) v.errors.password = "Use at least 10 characters.";
    if (d.password !== d.confirm) v.errors.confirm = "Passwords don't match.";
    if (Object.keys(v.errors).length) return lib.store.emit();
    v.busy = true; lib.store.emit();
    try { await S.auth.reset(form.dataset.token, d.password); Object.assign(v, { done: true, busy: false }); lib.store.emit(); } catch (e) { fail(e); }
  });
  lib.on("sign-out", async () => { try { await S.auth.logout(); } catch {} V.app.signedOut(); ui.toast("Signed out", "info"); });
  ui.dialogs["session-expired"] = () => ({ title: "Your session has ended", role: "alertdialog", body: `<p class="subtle">Sign in again to continue.</p>`, footer: ui.btn({ label: "Sign in", variant: "primary", act: "auth-relogin" }) });
  lib.on("auth-relogin", () => { lib.ui.state.afterLogin = lib.router.current.path; lib.ui.state.sessionExpired = true; ui.close(); V.app.signedOut(); });
})(V.features, V.ui, V.lib, V.services);
