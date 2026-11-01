#!/usr/bin/env node
// scripts/run.mjs — cross-platform launcher behind `npm run dev | start | build | test` (Node >= 18, no npm packages).
//
//   npm run dev     build the web app, migrate, start the API with auto-reload, rebuild the UI when apps/web/src changes,
//                   open the browser.            (VISIONTRA_PORT / PORT, default 8000; --no-open to skip the browser)
//   npm start       same, without auto-reload (the way start-visiontra.bat runs it)
//   npm run build   build apps/web/dist only
//   npm test        pytest + frontend unit tests
//
// Python is taken from .venv (Windows: .venv\Scripts\python.exe, else .venv/bin/python), then python3 / python.
// A Python process already listening on the port is stopped first, because an old server keeps serving old code.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, watch } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WIN = process.platform === "win32";
const mode = process.argv[2] || "dev";
const PORT = Number(process.env.VISIONTRA_PORT || process.env.PORT || 8000);
const HOST = process.env.VISIONTRA_HOST || "127.0.0.1";
const say = (m, c = 36) => console.log(`\x1b[${c}m[visiontra]\x1b[0m ${m}`);
const die = (m) => { console.error(`\x1b[31m[visiontra] ${m}\x1b[0m`); process.exit(1); };
const env = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };

function python() {
  const cands = [WIN ? join(ROOT, ".venv", "Scripts", "python.exe") : join(ROOT, ".venv", "bin", "python"), ...(WIN ? ["python", "py"] : ["python3", "python"])];
  for (const p of cands) {
    if (p.includes(".venv") && !existsSync(p)) continue;
    const r = spawnSync(p, ["-c", "import sys; print(sys.version_info >= (3, 11))"], { encoding: "utf8", env });
    if (r.status === 0 && r.stdout.trim() === "True") return p;
  }
  die("Python 3.11+ not found. Create the environment first:  python -m venv .venv  then  .venv\\Scripts\\pip install -r requirements.txt  (see README).");
}
const run = (cmd, args, opts = {}) => { const r = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT, env, ...opts }); return r.status === 0; };

function ensureDeps(py) {
  const ok = spawnSync(py, ["-c", "import fastapi, sqlalchemy, PIL, torch, torchvision, pillow_heif, uvicorn, alembic"], { cwd: ROOT, env }).status === 0;
  if (ok) return;
  say("Installing the Python packages VisionTra needs (first run or after an update)…", 33);
  if (!run(py, ["-m", "pip", "install", "-r", "requirements.txt"]) || !run(py, ["-m", "pip", "install", "--no-deps", "-e", "."])) die("pip install failed (see above).");
}
const build = (py) => { say("Building the web app…"); if (!run(py, [join("apps", "web", "build.py")])) die("The web app build failed (see above)."); };
const migrate = (py) => { say("Backing up and applying database migrations…"); if (!run(py, ["-c", "from visentra_api.main import migrate; migrate()"])) die("Database migration failed (see above)."); };

function stopOldServer() {
  if (WIN) {
    const ps = `$c = Get-NetTCPConnection -LocalPort ${PORT} -State Listen -ErrorAction SilentlyContinue; foreach ($x in $c) { $p = Get-Process -Id $x.OwningProcess -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -match '^(python|pythonw|uvicorn)') { Write-Output "stopping PID $($p.Id)"; Stop-Process -Id $p.Id -Force } elseif ($p) { Write-Output "BUSY $($p.ProcessName)" } }`;
    const r = spawnSync("powershell", ["-NoProfile", "-Command", ps], { encoding: "utf8" });
    if ((r.stdout || "").includes("BUSY")) die(`Port ${PORT} is used by another program (${r.stdout.trim()}). Close it or set VISIONTRA_PORT.`);
    if (r.stdout && r.stdout.trim()) say(r.stdout.trim(), 33);
  } else {
    const r = spawnSync("sh", ["-c", `command -v lsof >/dev/null && lsof -tiTCP:${PORT} -sTCP:LISTEN -c python || true`], { encoding: "utf8" });
    for (const pid of (r.stdout || "").split(/\s+/).filter(Boolean)) { say(`stopping old server PID ${pid}`, 33); try { process.kill(Number(pid)); } catch {} }
  }
}

function openWhenReady(url) {
  if (process.argv.includes("--no-open") || process.env.CI) return;
  let n = 0;
  const tick = () => http.get(`${url}/api/health/live`, (res) => { res.resume(); say(`VisionTra is running → ${url}`, 32);
      const opener = WIN ? ["cmd", ["/c", "start", "", url]] : process.platform === "darwin" ? ["open", [url]] : ["xdg-open", [url]];
      try { spawn(opener[0], opener[1], { stdio: "ignore", detached: true }).unref(); } catch {} })
    .on("error", () => { if (++n < 120) setTimeout(tick, 1000); });
  setTimeout(tick, 1500);
}

function serve(py, reload) {
  const args = ["-m", "uvicorn", "visentra_api.main:app", "--app-dir", join("apps", "api"), "--host", HOST, "--port", String(PORT)];
  if (reload) args.push("--reload", "--reload-dir", join("apps", "api"), "--reload-dir", join("ml", "visentra_ml"));
  say(`Starting the API on http://${HOST}:${PORT}/ ${reload ? "(auto-reload)" : ""} — Ctrl+C stops it.`, 32);
  if (existsSync(join(ROOT, "models", "detection", "VT-DET-0002", "model.pt"))) say("Detection model: VT-DET-0002", 32);
  else say("Detection model weights not found: images are saved for manual inspection only.", 33);
  const child = spawn(py, args, { stdio: "inherit", cwd: ROOT, env });
  const stop = () => { try { child.kill(); } catch {} process.exit(0); };
  process.on("SIGINT", stop); process.on("SIGTERM", stop);
  child.on("exit", (code) => process.exit(code ?? 0));
  openWhenReady(`http://${HOST === "0.0.0.0" ? "127.0.0.1" : HOST}:${PORT}`);
  if (reload) {           // rebuild the single-file UI when its source changes (debounced)
    let t = null;
    try { watch(join(ROOT, "apps", "web", "src"), { recursive: true }, () => { clearTimeout(t); t = setTimeout(() => { say("UI source changed — rebuilding (reload the page)…"); run(py, [join("apps", "web", "build.py")]); }, 400); }); }
    catch { say("File watching is not available here; run `npm run build` after UI changes.", 33); }
  }
}

const py = python();
if (mode === "build") { build(py); process.exit(0); }
if (mode === "test") {
  const a = run(py, ["-m", "pytest", "-q", "-p", "no:warnings"]);
  const b = run(process.execPath, ["--test", ...["tests/frontend/ac3d.test.js", "tests/frontend/aero.test.js"]]);
  process.exit(a && b ? 0 : 1);
}
ensureDeps(py); build(py); migrate(py); stopOldServer(); serve(py, mode === "dev");
