"""Bundle the frontend into dist/index.html (no Node toolchain required).

Also copies the lazily loaded 3D aircraft module and the vendored Three.js (MIT) to dist/vendor/, and stamps a
build id into the page (window.VT_BUILD) and dist/build.json. The server reads build.json when it starts, so the
page can tell when the running server process is older than the page it served (the server must be restarted).
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "src"
CSS = ["styles/tokens.css", "styles/components.css", "styles/motion.css", "styles/orbital.css", "styles/luxe.css", "styles/aero.css", "styles/inspect.css", "styles/elite.css", "styles/neon.css", "styles/signature.css", "styles/atelier.css", "styles/obsidian.css"]
JS = ["lib/core.js", "lib/icons.js", "lib/dom.js", "lib/charts.js", "lib/status.js", "lib/fx.js", "lib/fx3d.js", "lib/aircraft3d.js", "lib/ac3d.js", "lib/brand.js", "lib/aero.js",
      "services/api.js", "services/aero.js", "components/ui.js", "components/overlay.js", "components/premium.js",
      "features/_shared.js", "features/auth.js", "features/welcome.js", "features/intro.js", "features/aero_viewer.js", "features/aero_pages.js", "features/system_health.js", "features/admin.js",
      "app/shell.js", "app/main.js"]
missing = sorted({p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.js")} - set(JS))
if missing:
    raise SystemExit(f"Unbundled modules: {missing}")
css = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in CSS)
js = "\n".join(f"/* -- src/{p} -- */\n" + (ROOT / p).read_text(encoding="utf-8") for p in JS)
WEB = Path(__file__).resolve().parent
out = WEB / "dist"
out.mkdir(exist_ok=True)
vendor_src = {"three.module.min.js": WEB / "vendor/three/three.module.min.js",
              "addons/controls/OrbitControls.js": WEB / "vendor/three/addons/controls/OrbitControls.js",
              "addons/environments/RoomEnvironment.js": WEB / "vendor/three/addons/environments/RoomEnvironment.js",
              "THREE_LICENSE.txt": WEB / "vendor/three/LICENSE",
              "models/global_hawk.glb": WEB / "vendor/models/global_hawk.glb",
              "models/NOTICE.txt": WEB / "vendor/models/NOTICE.txt",
              "aircraft_scene.mjs": ROOT / "modules/aircraft_scene.mjs",
              "nexus_field.mjs": ROOT / "modules/nexus_field.mjs"}
h = hashlib.sha256()
for p in [ROOT / "index.html", *(ROOT / x for x in CSS), *(ROOT / x for x in JS), *vendor_src.values()]:
    h.update(p.read_bytes())
build = h.hexdigest()[:12]
head = ('<script type="importmap">{"imports":{"three":"/vendor/three.module.min.js?v=%s","three/addons/":"/vendor/addons/"}}</script>\n'
        '<script>window.VT_BUILD="%s";</script>\n' % (build, build))
html = (ROOT / "index.html").read_text(encoding="utf-8").replace("<style>", head + "<style>", 1).replace("/*__CSS__*/", css).replace("/*__JS__*/", js.replace("</script", "<\\/script"))
vd = out / "vendor"
# Refresh the fixed vendor manifest in place: deleting this directory races
# a development watcher and briefly breaks lazy imports in open browsers.
for rel, src in vendor_src.items():
    (vd / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, vd / rel)
(out / "index.html").write_text(html, encoding="utf-8")
(out / "build.json").write_text(json.dumps({"build": build, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "index_bytes": len(html.encode())}, indent=1), encoding="utf-8")
print(f"built dist/index.html ({len(html) // 1024} KB, {len(JS)} modules) + dist/vendor ({sum(p.stat().st_size for p in vd.rglob('*') if p.is_file()) // 1024} KB, lazy) - build {build}")
