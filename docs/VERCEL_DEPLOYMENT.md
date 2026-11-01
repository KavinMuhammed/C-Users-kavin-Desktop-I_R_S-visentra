# Source restoration and Vercel deployment

The application is `apps/api/visentra_api/main.py`, exporting the existing `app` object. Its Vercel module entrypoint is `apps.api.visentra_api.main:app`. Set the Vercel project Root Directory to the repository root. The Python requirements install this checkout so absolute `visentra_api` and `visentra_ml` imports used by migrations and inference remain available. The build command runs the existing `apps/web/build.py` bundler. Dashboard build-command overrides take precedence and must be checked.

The original repository upload included root-level files only. This restoration adds the existing backend, migrations, frontend and attributed 3D assets, ML implementation/configuration, scripts, infrastructure and test source. It preserves existing root files except the reviewed ignore, packaging and entrypoint changes. No detector training or source redesign is part of this restoration.

## Deployment gates

An entrypoint fix does not establish a working deployment. Verify the Vercel build and runtime logs for the exact commit before use.

- Install and bundle the pinned dependencies on Vercel's supported Linux/Python runtime. PyTorch, torchvision and OCR dependencies may exceed serverless bundle limits. The local Windows build does not establish Linux compatibility.
- Provision a persistent external database and object storage. Local SQLite, uploaded files and local report storage must not be treated as durable on a serverless filesystem. Configure application secrets privately in the hosting settings, not in Git.
- The application creates a data directory at startup and runs migrations in its lifespan. Review writable-path configuration, backup behavior and concurrent migration behavior before production use. Never point a deployment trial at the existing production database.
- Detector and gate weight binaries are intentionally excluded. Preserve and provision the existing verified weights through an appropriate private deployment mechanism. Configuration and checksums are included; no replacement model or fabricated inference is supplied.
- Background model warming and in-process analysis need lifecycle, timeout, memory and cold-start validation on the chosen host.
- Dataset browser and training workflows require separately provisioned licensed data. Raw datasets and inspection databases are intentionally absent.

If the unchanged inference stack cannot fit Vercel, use a compatible persistent Python/container backend and integrate the frontend deliberately. Do not claim full inference works merely because an entrypoint is detected.

Official configuration reference: https://vercel.com/docs/frameworks/backend/fastapi
