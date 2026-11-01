"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def resolve_database_url(value: str) -> str:
    """Resolve SQLite files against the installation, not the launch directory."""
    url = make_url(value)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return value
    database = url.database
    if database.startswith("file:"):
        if url.query.get("mode") == "memory" or database == "file::memory:":
            return value
        database = "file:" + project_path(database[5:]).as_posix()
    else:
        database = project_path(database).as_posix()
    return url.set(database=database).render_as_string(hide_password=False)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return default if not v else int(v)


def _list(name: str, default: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(name, default).split(",") if x.strip()]


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing environment variables win."""
    # A random launch directory must never select a different database or secret.
    for candidate in (PROJECT_ROOT / ".env",):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                import shlex
                # Preserve quoted # characters, strip comments in the shipped template.
                lexer = shlex.shlex(v.strip(), posix=True)
                lexer.whitespace_split = True
                lexer.escape = ""  # Windows paths contain literal backslashes.
                os.environ.setdefault(k.strip(), " ".join(lexer))
            break


@dataclass
class Settings:
    env: str = "development"
    database_url: str = ""
    data_dir: Path = Path("data")
    storage_backend: str = "local"  # local | s3
    storage_endpoint: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "visentra"
    model_storage_path: Path = Path("data/models")
    session_secret: str = ""
    session_days: int = 7
    cookie_secure: bool = False
    cors_origins: list[str] = field(default_factory=list)
    max_upload_mb: int = 20
    max_zip_mb: int = 500
    max_zip_files: int = 20000
    max_image_pixels: int = 60_000_000
    min_image_side: int = 32
    allowed_source_hosts: list[str] = field(default_factory=list)
    allow_private_network: bool = False
    allow_insecure_http: bool = False
    worker_mode: str = "inline"  # inline | external
    worker_threads: int = 1
    log_level: str = "INFO"
    torch_threads: int = 0
    registration_open: bool = True
    reset_link_in_log: bool = True        # no mail transport: the reset link goes to the server log (off by default in production)
    job_stale_seconds: int = 120          # a running job whose heartbeat is older than this is reconciled
    worker_stale_seconds: int = 60        # a worker heartbeat older than this counts as offline
    device_heartbeat_timeout: int = 60    # seconds without a device heartbeat before OFFLINE
    max_job_attempts: int = 1             # training is not resumed mid-epoch; retries are explicit
    review_threshold_default: float = 0.6
    feature_flags: dict = field(default_factory=dict)
    demo_samples_allowed: bool = False      # Phase 12: synthetic SAMPLE inspection records may be shown in the browser (never in production)
    # Phase 8 — development-only experimental detection (docs/PHASE_8_SECURITY.md). Off by default.
    experimental_detection: bool = False
    experimental_users: list[str] = field(default_factory=list)   # allow-listed account emails (lower-case)
    experimental_allow_remote: bool = False                       # default: loopback clients only
    experimental_timeout_s: float = 30.0
    experimental_max_pixels: int = 40_000_000
    detection_models_dir: Path = Path("models/detection")
    # Phase 13 — operational, review-assisted defect detection on every new inspection
    inspection_model: str = "auto"        # "auto" (newest eligible bundle) | a model id such as VT-DET-0002 | "off"
    inspection_analysis_timeout_s: float = 60.0
    domain_gate: str = "auto"             # "auto" (newest models/gate/VT-GATE-*) | a gate id | "off"
    domain_gate_dir: Path = Path("models/gate")
    # Phase 10 — persisted inspections (API-01 policy, docs/API_ARCHITECTURE.md §3)
    inspection_min_side: int = 64
    inspection_max_upload_mb: int = 5
    inspection_max_side: int = 12_000
    inspection_rate_per_min: int = 30
    storage_intent_grace_s: int = 900        # a pending write intent older than this is reconciled

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    _load_dotenv()
    data_dir = project_path(os.environ.get("VISENTRA_DATA_DIR") or "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("JWT_SECRET") or os.environ.get("SESSION_SECRET")
    env = os.environ.get("VISENTRA_ENV", "development")
    if env == "production" and secret and (len(secret) < 32 or "CHANGE_ME" in secret.upper()):
        # a template placeholder or a short secret would let anyone who guesses it forge sessions
        raise RuntimeError("JWT_SECRET must be a random value of at least 32 characters in production (not the template placeholder).")
    if not secret:
        if env == "production":
            raise RuntimeError("JWT_SECRET (session secret) must be set in production.")
        secret_file = data_dir / ".dev_secret"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_urlsafe(48))
            os.chmod(secret_file, 0o600)
        secret = secret_file.read_text().strip()
    return Settings(
        env=env,
        database_url=resolve_database_url(os.environ.get("DATABASE_URL") or f"sqlite:///{(data_dir / 'visentra.db').as_posix()}"),
        data_dir=data_dir,
        storage_backend=os.environ.get("STORAGE_BACKEND", "local"),
        storage_endpoint=os.environ.get("STORAGE_ENDPOINT", ""),
        storage_access_key=os.environ.get("STORAGE_ACCESS_KEY", ""),
        storage_secret_key=os.environ.get("STORAGE_SECRET_KEY", ""),
        storage_bucket=os.environ.get("STORAGE_BUCKET", "visentra"),
        model_storage_path=project_path(os.environ.get("MODEL_STORAGE_PATH") or data_dir / "models"),
        session_secret=secret,
        session_days=_int("SESSION_DAYS", 7),
        cookie_secure=_bool("COOKIE_SECURE", env == "production"),
        demo_samples_allowed=env != "production" and _bool("VISIONTRA_DEMO_SAMPLES", False),
        cors_origins=_list("CORS_ORIGINS", ""),
        max_upload_mb=_int("MAX_UPLOAD_MB", 20),
        max_zip_mb=_int("MAX_ZIP_MB", 500),
        max_zip_files=_int("MAX_ZIP_FILES", 20000),
        max_image_pixels=_int("MAX_IMAGE_PIXELS", 60_000_000),
        min_image_side=_int("MIN_IMAGE_SIDE", 32),
        allowed_source_hosts=_list(
            "ALLOWED_SOURCE_HOSTS",
            "datasets-server.huggingface.co,huggingface.co,hf.co,cdn-lfs.huggingface.co,cdn-lfs.hf.co,"
            "cas-bridge.xethub.hf.co,upload.wikimedia.org,storage.googleapis.com,raw.githubusercontent.com",
        ),
        allow_private_network=_bool("ALLOW_PRIVATE_NETWORK", False),
        allow_insecure_http=_bool("ALLOW_INSECURE_HTTP", False),
        worker_mode=os.environ.get("WORKER_MODE", "inline"),
        worker_threads=_int("WORKER_THREADS", 1),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        torch_threads=_int("TORCH_THREADS", 0),
        registration_open=_bool("REGISTRATION_OPEN", True),
        # a reset link is a bearer credential: anyone who can read production logs could take over the account
        reset_link_in_log=_bool("PASSWORD_RESET_LINK_IN_LOG", env != "production"),
        job_stale_seconds=_int("JOB_STALE_SECONDS", 120),
        worker_stale_seconds=_int("WORKER_STALE_SECONDS", 60),
        device_heartbeat_timeout=_int("DEVICE_HEARTBEAT_TIMEOUT", 60),
        max_job_attempts=_int("MAX_JOB_ATTEMPTS", 1),
        experimental_detection=_bool("VISIONTRA_EXPERIMENTAL_DETECTION", False),
        experimental_users=[e.lower() for e in _list("VISIONTRA_EXPERIMENTAL_USERS", "")],
        experimental_allow_remote=_bool("VISIONTRA_EXPERIMENTAL_ALLOW_REMOTE", False),
        experimental_timeout_s=float(os.environ.get("VISIONTRA_EXPERIMENTAL_TIMEOUT_S", "30")),
        experimental_max_pixels=_int("VISIONTRA_EXPERIMENTAL_MAX_PIXELS", 40_000_000),
        inspection_model=os.environ.get("VISIONTRA_INSPECTION_MODEL", "auto").strip() or "auto",
        inspection_analysis_timeout_s=float(os.environ.get("VISIONTRA_INSPECTION_ANALYSIS_TIMEOUT_S", "60")),
        domain_gate=os.environ.get("VISIONTRA_DOMAIN_GATE", "auto").strip() or "auto",
        domain_gate_dir=project_path(os.environ.get("VISIONTRA_DOMAIN_GATE_DIR") or PROJECT_ROOT / "models" / "gate"),
        inspection_min_side=_int("INSPECTION_MIN_SIDE", 64),
        inspection_max_upload_mb=_int("INSPECTION_MAX_UPLOAD_MB", 5),
        inspection_max_side=_int("INSPECTION_MAX_SIDE", 12_000),
        inspection_rate_per_min=_int("INSPECTION_RATE_PER_MIN", 30),
        storage_intent_grace_s=_int("STORAGE_INTENT_GRACE_S", 900),
        detection_models_dir=project_path(os.environ.get("VISIONTRA_DETECTION_MODELS_DIR") or PROJECT_ROOT / "models" / "detection"),
        feature_flags={
            "devices": _bool("FEATURE_DEVICES", True),
            "batch_inference": _bool("FEATURE_BATCH_INFERENCE", True),
            "url_sources": _bool("FEATURE_URL_SOURCES", True),
            "explanations": _bool("FEATURE_EXPLANATIONS", True),
            "realtime": _bool("FEATURE_REALTIME", True),
        },
    )


settings = load_settings()
