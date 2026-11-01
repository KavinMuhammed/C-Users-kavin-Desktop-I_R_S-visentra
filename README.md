# VisionTra — Smarter Visual Inspection of Aircraft

VisionTra detects, classifies and documents aircraft surface defects — **cracks, dents and corrosion** — in photos or drone frames. Upload an image: it is saved as an inspection, analysed by the detection model, and opened with every detection drawn on the image. An engineer confirms, rejects or reclassifies each detection, marks anything the model missed, signs off, and downloads a PDF report that records the image checksum, the model and every decision.

VisionTra is decision support for qualified maintenance engineers. It does not determine airworthiness, does not replace a qualified inspector, and holds no aviation certification.

## Current status (verified 2026-10-19)

| | Status | Evidence |
|---|---|---|
| Application | **Complete within the tested scope**: upload → relevance check → detection → review → sign-off → PDF → history, on SQLite and PostgreSQL 16 | [RELEASE_READINESS_REPORT](docs/RELEASE_READINESS_REPORT.md) |
| Verification | pytest 315/315 (PostgreSQL 16), 314 + 1 skipped (SQLite); frontend unit 10/10; browser workflows 10/10, 13/13, 13/13, 12/12; laptop start, inspection, PDF and restart verified | [TEST_RESULTS](docs/TEST_RESULTS.md) |
| Model | **VT-DET-0002 retained** (no candidate met the release rule; test split untouched in Phase 18) | [PHASE18_MODEL_SELECTION_REPORT](docs/PHASE18_MODEL_SELECTION_REPORT.md), [MODEL_CARD](docs/MODEL_CARD.md) |
| Operational readiness | Appropriate for **supervised demonstration and decision-support evaluation** only; validation dent AP50 is 0.05 and whole-nose dents are mostly missed | [RELEASE_BLOCKERS](docs/RELEASE_BLOCKERS.md) |

Architecture overview: [ARCHITECTURE](docs/ARCHITECTURE.md). Demonstration script: [DEMO_AND_COMPETITION_GUIDE](docs/DEMO_AND_COMPETITION_GUIDE.md).

## Quick start

```bash
npm run dev        # builds the UI, migrates the database, starts the API with auto-reload and opens http://127.0.0.1:8000
npm start          # the same without auto-reload (what start-visiontra.bat does on Windows)
npm test           # pytest + frontend unit tests
npm run build      # rebuild apps/web/dist only
```

Needs Node 18+ and Python 3.11+ with the packages installed once (`python -m venv .venv`, then
`.venv\Scripts\pip install -r requirements.txt` and `pip install --no-deps -e .`; `npm run dev` installs them itself if they
are missing). `VISIONTRA_PORT` changes the port. Docker: `docker compose -f infra/docker-compose.yml --env-file .env up --build`
(PostgreSQL + MinIO + worker; the image now includes the VT-DET-0002 detector and the VT-GATE-0002 surface check).

## How to perform an inspection

1. Start the server (`start-visiontra.bat` on Windows, or the commands below) and open http://127.0.0.1:8000; create an account.
2. **New Inspection** → choose or drop an image (JPEG/PNG/WebP/HEIC/TIFF…, up to 5 MB) and optionally the aircraft and component.
3. VisionTra saves the image, runs the aircraft-surface relevance check (VT-GATE-0002) and, if relevant or uncertain, the detector (VT-DET-0002). Outcomes are shown truthfully: *AI candidates to review*, *Detection ran: no candidates at the configured thresholds* (not proof of no defects), *Not analysed* (relevance check rejected the image; an engineer can still inspect manually or run detection with a recorded reason), or *Detection failed* (the inspection is kept; run it again).
4. Read **AI result in plain words** first: for dent, crack and corrosion it says *likely*, *possible*, *unlikely* or *not found by the AI*, with how often such boxes were real on validation images. The image itself shows numbered markers only; names and scores are in the list.
5. Review every candidate: confirm, reject, reclassify or mark for further review (keys J/K, C, R, F). Unlikely candidates (usually false alarms) are grouped and can be rejected together after a look. **Reclassify → Other** asks for the type (stain, scratch, paint damage, fastener, old repair, dirt, or *not damage*, which is recorded as a rejection), so "Other" is never unexplained. Add missed defects with **Mark missed defect**.
6. **Sign off** (refused until every candidate is decided; zero-candidate and manual-only inspections need an explicit acknowledgement), then **Generate PDF report**. The report pins the image checksum, model version, every decision and its author.

| Page | What it does |
|---|---|
| Dashboard `/inspection` | Drop, paste or choose images (several at once). Each becomes an inspection and is analysed automatically. Shows key figures and recent inspections. |
| Inspection `/inspection/:id` | The image with its detections (class, confidence). Zoom, pan, filter by class or confidence. Confirm / reject / reclassify / further review (keys J K C R F), "Reject the rest", mark a missed defect (M), sign off, PDF report, re-run detection. |
| Inspections `/inspection/history` | Search and filter all inspections (status, confirmed defect type). |
| Detection model `/inspection/model` | The model in use: classes, held-out test results per class, thresholds, training data, known limitations. |
| Datasets `/inspection/datasets` | The datasets the model was trained on (image browser with labels) and the public datasets you can add. |
| System health, Audit log, Settings | Service status, the append-only audit trail, profile / workspace members / appearance. |

**Detection model.** `models/detection/VT-DET-0002` — a YOLOX detector for crack, dent and corrosion, trained on VT-DS-001 and VT-DS-002 (see [MODEL_CARD](docs/MODEL_CARD.md)). `VISIONTRA_INSPECTION_MODEL` selects the model: `auto` (default: the newest eligible bundle), a model id, or `off`. Weights (`model.pt`) are not in git; they are delivered with the bundle and verified by SHA-256 before loading.

**Adding datasets.** Download a registered public dataset's YOLO export yourself (the hosts are not reachable from the server), then run `python scripts/datasets/import_dataset.py VT-DS-003 export.zip`, `python scripts/datasets/run_prep.py --prep VT-PREP-002` and train the next model version (`scripts/ml/train_detector.py`). See [PHASE_13_COMPLETION](docs/PHASE_13_COMPLETION.md).

**History.** Phases 1–12 built the platform, persistence (Phase 10), stored review and reports (Phase 11) and quality work (Phase 12); Phase 13 made it a single-purpose inspection product with operational detection. Decisions: [PHASE_13_DECISIONS](docs/PHASE_13_DECISIONS.md). Architecture: `docs/SYSTEM_ARCHITECTURE.md` (check with `python scripts/architecture_check.py`).

## Backend architecture

Modular monolith (FastAPI + SQLAlchemy 2 + Alembic) with database-backed workers. Training, evaluation, collection and batch inference run in workers, never in an HTTP request; interactive single-image inference runs in the API threadpool.

| Area | What is enforced |
|---|---|
| API contract | `/api/v1/...` aliases `/api/...`. Errors are `{"error": {code, message, details, request_id[, field]}}` with stable codes (`DATASET_INSUFFICIENT`, `MODEL_UNAVAILABLE`, `MODEL_PROMOTION_BLOCKED`, `ARTIFACT_CHECKSUM_MISMATCH`, `REVIEW_CONFLICT`, `DEVICE_AUTH_INVALID`, …). `X-Request-ID` is echoed or generated. |
| Workspaces & RBAC | Roles OWNER, ADMIN, ML_ENGINEER, OPERATOR, REVIEWER, VIEWER map to project permissions; explicit project membership can only raise a role. Other workspaces' resources return 404. |
| Datasets | Versions are frozen at creation: canonical JSON manifest in object storage + SHA-256; ORM guards reject edits. Readiness analyses are persisted with a versioned policy (`READINESS_*` env vars) and history. |
| Training | Gate on readiness (INVALID blocks; INSUFFICIENT needs an audited override). Frozen config snapshot, seed, environment record (OS, CPU, torch/CUDA/cuDNN, GPU, device, worker), per-epoch metrics, persisted log (`/api/training-runs/{id}/logs`), cooperative cancel, error codes (`CUDA_OUT_OF_MEMORY`, `NON_FINITE_LOSS`, `PRETRAINED_WEIGHTS_UNAVAILABLE`, `DATA_LOAD_ERROR`, `WORKER_LOST`, …). `Idempotency-Key` supported. |
| Worker liveness | Each worker heartbeats every 10 s. Running jobs whose worker process is gone (same host, dead PID) or silent past `JOB_STALE_SECONDS` are failed with `WORKER_LOST` — never retried silently, never reported as successful. `python -m visentra_api.worker --check` is the container health check. |
| Model registry | `registry_status`: TRAINING, EVALUATING, CANDIDATE, VALIDATED, APPROVED, PRODUCTION, RETIRED, FAILED. Promotion requires owner/admin, a held-out evaluation, an existing artifact with a matching checksum that loads with matching metadata; it locks the project row and appends to the production history. Rollback (`POST /api/projects/{id}/production/rollback`) re-verifies and records a reason. |
| Inference | Every prediction stores `threshold_used`, `decision` (`AUTO_ACCEPT` / `REVIEW_REQUIRED`), source (upload/batch/device), latency breakdown and model version. No production model → `MODEL_UNAVAILABLE`. Grad-CAM on demand (`POST /api/predictions/{id}/explanation`), `EXPLANATION_UNSUPPORTED` for architectures without it. Batch: `POST /api/projects/{id}/batch-inference`. |
| Reviews | Claim / release / approve / correct / reject / escalate with conditional updates (`REVIEW_CONFLICT` on races), full event history, and a feedback summary showing which human labels are waiting for the next dataset version. |
| Health | `/api/health/live` (process) and `/api/health/ready` (measured DB round-trip, storage write/read, worker heartbeat, ML runtime; `degraded` vs `unavailable`). `/api/system/capabilities`, `/api/system/version`. |
| Audit | Append-only, written in the same transaction as the action; `/api/audit/{id}` and `/api/audit-export.csv` (formula-injection safe). |

## Run locally (SQLite, local storage, in-process worker)

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                       # installs PyTorch (CPU/GPU wheel per your platform)
pip install --no-deps -e .
cp .env.example .env                                       # optional; defaults work for local use
python apps/web/build.py                                   # bundles the UI into apps/web/dist/index.html
cd apps/api && alembic upgrade head && cd ../..            # migrations (also applied automatically at startup, after an automatic SQLite backup)
uvicorn visentra_api.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and create an account. `scripts/dev.sh` runs the last four steps.

**Windows: after every update, restart the server.** Python code is loaded once when uvicorn starts, so a server started before an update serves the new page with the old API (the page then shows a banner: *"The VisionTra server is running an older version than this page"*). Double-click `start-visiontra.bat` in the project folder (or run `powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1`): it stops whatever is listening on port 8000, rebuilds the frontend, starts the server and opens the browser. Then hard-refresh (Ctrl+F5) any open tab. Details: [PHASE_14_UI_RECOVERY](docs/PHASE_14_UI_RECOVERY.md).

**Minimum runtime:** Python ≥ 3.11 (SQLAlchemy 2.1, NumPy 2.x) and PyTorch 2.14 with torchvision 0.29 (CPU wheels work; a GPU is optional). Node ≥ 20 is needed only for the frontend and browser tests. The dataset views need the repository's `datasets/` folder with `datasets/raw` acquired (`scripts/datasets/acquire.py`); without it, the Datasets page reports the registry as unavailable instead of inventing data.

**Backups and storage checks (Phase 10).** `python scripts/db_backup.py --objects` copies the database and the image store to `data/backups/`; `python scripts/reconcile_storage.py --verify --dry-run` reports any image that is missing or any interrupted upload. Restore steps: [PHASE_10_RECOVERY](docs/PHASE_10_RECOVERY.md).

**Detection on your machine.** With `models/detection/VT-DET-0002/model.pt` present, new inspections are analysed automatically (measured on the project laptop, CPU PyTorch: median 122 ms, max 242 ms model inference over 7 stored inspections; first request also loads the model). Without it, images are saved for manual inspection and the dashboard says that no model is installed.

## Production-style stack (PostgreSQL + MinIO + separate worker)

```bash
cp .env.example .env    # set JWT_SECRET, and add POSTGRES_PASSWORD and MINIO_ROOT_PASSWORD
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

Set `COOKIE_SECURE=true` behind HTTPS. With `WORKER_MODE=external`, run `python -m visentra_api.worker` (the compose file does this).

## Tests

```bash
node --test tests/frontend/*.test.js           # frontend unit tests (no npm packages)
python scripts/datasets/verify_prepared_files.py   # read-only re-check of all 11,705 prepared dataset files (≈1 min)
python scripts/perf/api_bench.py --base http://127.0.0.1:8000 --n 200   # API latency (development server only: creates data)
pytest                                        # API, security, ML, platform governance, inspections, migrations and the full workflow (real training)
TEST_DATABASE_URL=postgresql+psycopg://user@/db?host=/tmp pytest   # same suite on PostgreSQL (use a freshly created, empty database for every run)
# browser end-to-end test (Node + Playwright, a running server; AXE_PATH enables the accessibility audit)
VISENTRA_URL=http://127.0.0.1:8000 node tests/ui/aero_e2e.js            # with the detection model (default image: a real, traceable aircraft photo from VT-CLEAN-001)
VISENTRA_URL=http://127.0.0.1:8001 node tests/ui/aero_e2e.js --no-model   # server started with VISIONTRA_INSPECTION_MODEL=off
VISENTRA_URL=http://127.0.0.1:8000 IMG=path/to/aircraft.jpg node tests/ui/p14_e2e.js            # loading/error recovery, stale-server banner, 3D aircraft
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Banner "The VisionTra server is running an older version than this page" | Restart with `start-visiontra.bat`, then Ctrl+F5. |
| "Not analysed — the aircraft-surface check rejected this image" | The relevance gate judged it not an aircraft surface (drawings and non-aircraft photos are rejected by design). Inspect manually, or **Run detection anyway** with a reason. |
| "Detection ran: no candidates" | The model found nothing above its validation-selected thresholds. This is not proof the surface is defect-free; acknowledge your visual check before sign-off. |
| "Detection failed" (`MODEL_UNAVAILABLE`, `INFERENCE_FAILED`, `ANALYSIS_BUSY`, `INVALID_MODEL_OUTPUT`) | The inspection is saved; check System Health, then **Run detection again**. `MODEL_UNAVAILABLE` usually means `model.pt` is missing or its checksum differs. |
| Sign-off refused (`REVIEW_INCOMPLETE`) | Decide every AI candidate first ("Reject the rest" handles the remainder in one step). |
| `/api/v1/health/ready` shows `degraded` | Read the failing check (database, storage, model, ML runtime); `VISIONTRA_INSPECTION_MODEL=off` intentionally degrades the model check. |

## Known limitations

* Model quality is limited by data: validation AP50 0.21 overall (crack 0.05, dent 0.05, corrosion 0.53); per-class precision/recall in [MODEL_CARD](docs/MODEL_CARD.md). Whole-nose and medium-distance dents (e.g. the Boeing nose dent INS-0005) are usually missed.
* Confidence is an uncalibrated model score, not a probability that a defect is present.
* Crack/dent training images come from one hangar collection and corrosion from another (class/source confound); no engineer-verified whole-aircraft dataset exists yet.
* The held-out test split is from one phone session and is not aircraft-independent.
* PostgreSQL and MinIO are supported and tested in CI-style runs; the laptop deployment uses SQLite and local storage.

## Environment variables

See `.env.example` for the full list with comments: `DATABASE_URL`, `JWT_SECRET`, `STORAGE_*`, `MODEL_STORAGE_PATH`, `WORKER_MODE`, upload limits, and `ALLOWED_SOURCE_HOSTS` (the only hosts the server will fetch dataset images from).

## Connect a camera or edge device

1. Devices → Register device. Copy the token (shown once).
2. On the machine next to the camera: `VISIONTRA_DEVICE_TOKEN=vdt_… python scripts/device_agent.py --server https://your-server --folder ./captures` (or `--webcam 0` with `opencv-python` installed; `psutil` adds CPU/memory/temperature telemetry).
3. The device turns Connected on its first heartbeat; captures run the project's production (or pinned) model and land in prediction history with source “device”.

## Layout

```
apps/api/visentra_api   FastAPI app: routers, services (ingestion, collection, datasets, readiness, manifest, training, inference, batch, devices, hardware, notify), security/RBAC, storage, jobs + worker
scripts/device_agent.py reference device agent (stdlib only)
apps/api/alembic        database migrations
apps/web/src            UI source (design tokens, components, features, services); build.py → dist/index.html
  features/insp_*.js    aircraft-inspection pages; services/inspection.js domain + contracts; lib/pdf.js report writer
ml/visentra_ml          PyTorch architectures, preprocessing, training, evaluation, inference, Grad-CAM
infra/                  Dockerfile and docker-compose
tests/                  pytest suites, frontend unit tests (tests/frontend) and the browser workflow tests (tests/ui)
```
