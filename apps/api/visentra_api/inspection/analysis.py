"""Operational, review-assisted defect detection for inspections (Phase 13).

Every new inspection is analysed by the configured detection model bundle (``VISIONTRA_INSPECTION_MODEL``,
default ``auto`` = the newest eligible bundle in ``models/detection``). A bundle is eligible when its
checksums verify, its readiness is EVALUATED_LIMITED or CANDIDATE_FOR_INTEGRATION and it declares at least
one supported class from the review taxonomy (crack, dent, corrosion).

Policy (PHASE_13_DECISIONS AD-13-01): an EVALUATED_LIMITED model may propose defects, but every proposal
is an immutable ``defect_predictions`` row that stays *pending* until a person confirms, rejects or
reclassifies it. Nothing the model outputs is a finding, and sign-off still requires a complete review.

Analysis runs inside the creating request, before the inspection's transaction, so the inspection, its
analysis run and its predictions are committed together (or the inspection is committed as
``analysis_failed`` with the error code, and can be re-analysed with EP-03).

One model instance is shared by the process. Inference is serialised by a semaphore; a request that cannot
get the slot within the timeout records ``ANALYSIS_BUSY`` rather than queueing indefinitely.
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (AnalysisMedia, AnalysisRun, Dataset, DatasetVersion, DefectPrediction, Experiment, Inspection, InspectionMedia, Job, ModelVersion,
                      Project, REVIEW_CATEGORIES, now, uid)

log = logging.getLogger("visentra.analysis")

MODEL_ID_RE = re.compile(r"^VT-DET-(\d{4})$")
OPERATIONAL_READINESS = ("EVALUATED_LIMITED", "CANDIDATE_FOR_INTEGRATION")
REGISTRY_PROJECT = "VisionTra detection model registry"
POLICY = ("AI proposals only. Every detection stays pending until an engineer confirms, rejects or reclassifies it; "
          "the model does not decide serviceability or airworthiness.")


class AnalysisFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Result:
    output: dict
    prep: dict | None
    info: dict | None
    timings: dict = field(default_factory=dict)
    gate: dict | None = None
    out_of_domain: bool = False


def _eligible(meta: dict) -> str | None:
    """None when the bundle may serve operational, review-assisted analysis; otherwise the reason."""
    if meta.get("readiness") not in OPERATIONAL_READINESS:
        return f"readiness {meta.get('readiness')} is not one of {OPERATIONAL_READINESS}"
    sup = meta.get("supported_classes") or []
    if not sup:
        return "the model declares no supported classes"
    unknown = set(meta.get("classes") or []) - set(REVIEW_CATEGORIES)
    if unknown:
        return f"classes outside the review taxonomy: {sorted(unknown)}"
    return None


def candidates(root: Path) -> list[tuple[str, Path, dict]]:
    out = []
    for d in sorted(root.glob("VT-DET-*")) if root.is_dir() else []:
        if not MODEL_ID_RE.match(d.name) or not (d / "metadata.json").is_file() or not (d / "model.pt").is_file():
            continue
        try:
            meta = json.loads((d / "metadata.json").read_text())
        except (OSError, ValueError):
            continue
        out.append((d.name, d, meta))
    return out


def resolve() -> tuple[str | None, Path | None, str | None]:
    """(model_id, bundle, reason-if-none) for the configured setting. Reads metadata only (no torch)."""
    want = (settings.inspection_model or "auto").strip()
    if want.lower() == "off":
        return None, None, "Automatic analysis is switched off (VISIONTRA_INSPECTION_MODEL=off)."
    cands = candidates(Path(settings.detection_models_dir))
    if want.lower() != "auto":
        for mid, d, meta in cands:
            if mid == want:
                why = _eligible(meta)
                return (mid, d, None) if why is None else (None, None, f"{mid} is not eligible: {why}.")
        return None, None, f"Model bundle {want} was not found (or its weights file is missing)."
    for mid, d, meta in sorted(cands, key=lambda c: c[0], reverse=True):
        if _eligible(meta) is None:
            return mid, d, None
    return None, None, "No eligible detection model bundle is installed (needs EVALUATED_LIMITED or better with supported classes)."


class Engine:
    def __init__(self, model_id: str, bundle: Path):
        self.model_id, self.bundle = model_id, bundle
        self.state, self.error = "not_loaded", None
        self.predictor = None
        self.meta: dict | None = None
        self.weights_sha256: str | None = None
        self.load_ms: float | None = None
        self._lock = threading.Lock()
        self._slot = threading.BoundedSemaphore(1)

    def load(self) -> None:
        with self._lock:
            if self.state == "ready":
                return
            if self.state == "failed":
                raise AnalysisFailed("MODEL_UNAVAILABLE", self.error or "The detection model failed to load.")
            t0 = time.perf_counter()
            try:
                from visentra_ml.detection import artifact as A
                from visentra_ml.detection.inference import Predictor
                v = A.verify(self.bundle)
                if not v["ok"]:
                    raise AnalysisFailed("MODEL_UNAVAILABLE", "Model bundle failed verification: " + "; ".join(v["problems"])[:300])
                why = _eligible(v["meta"])
                if why:
                    raise AnalysisFailed("MODEL_UNAVAILABLE", f"Model is not eligible: {why}")
                self.predictor = Predictor(self.bundle, threads=None)
                self.meta = self.predictor.meta
                self.weights_sha256 = v["meta"]["files"]["model.pt"]["sha256"]
            except AnalysisFailed as ex:
                self.state, self.error = "failed", str(ex)
                log.error("inspection model load failed", extra={"model_id": self.model_id})
                raise
            except Exception as ex:                      # noqa: BLE001 — any load failure is reported, never raised raw
                self.state, self.error = "failed", f"{type(ex).__name__}: {str(ex)[:200]}"
                log.exception("inspection model load failed", extra={"model_id": self.model_id})
                raise AnalysisFailed("MODEL_UNAVAILABLE", "The detection model could not be loaded.") from None
            self.load_ms = (time.perf_counter() - t0) * 1000
            self.state = "ready"

    def detect(self, data: bytes, ref: str) -> Result:
        from PIL import Image as PILImage
        from visentra_ml.detection.output import InvalidModelOutput

        self.load()
        if not self._slot.acquire(timeout=settings.inspection_analysis_timeout_s):
            raise AnalysisFailed("ANALYSIS_BUSY", "The detector was busy for too long. The inspection was saved; run the analysis again.")
        try:
            timing: dict[str, Any] = {}
            t0 = time.perf_counter()
            with PILImage.open(io.BytesIO(data)) as im:
                im.load()
                dets, prep, info = self.predictor.raw_detections(im, timing)
            th = self.meta["thresholds"]
            from visentra_ml.detection import output as O
            try:
                out = O.build(self.meta, {"ref": ref, **info}, prep, dets, score_threshold=th["score_threshold"],
                              review_threshold=th.get("review_threshold"), nms_iou=th["nms_iou"])
            except InvalidModelOutput as ex:
                raise AnalysisFailed("INVALID_MODEL_OUTPUT", f"The model produced output that failed validation ({str(ex)[:120]}).") from None
            timing["total_ms"] = (time.perf_counter() - t0) * 1000
            return Result(out, prep, info, timing)
        except AnalysisFailed:
            raise
        except InvalidModelOutput as ex:
            raise AnalysisFailed("INVALID_MODEL_OUTPUT", f"The model produced output that failed validation ({str(ex)[:120]}).") from None
        except Exception as ex:                          # noqa: BLE001
            log.exception("inspection inference failed", extra={"model_id": self.model_id})
            raise AnalysisFailed("INFERENCE_FAILED", f"Detection failed on this image ({type(ex).__name__}).") from None
        finally:
            self._slot.release()

    def describe(self) -> dict:
        meta = self.meta or _read_meta(self.bundle)
        ev = _evaluation(self.bundle)
        return {"available": self.state != "failed", "state": self.state, "model_id": self.model_id, "version": meta.get("version"),
                "architecture": f"{meta.get('architecture')}-{meta.get('variant')}", "readiness": meta.get("readiness"),
                "classes": meta.get("supported_classes") or [], "input_size": meta.get("input_size"),
                "thresholds": meta.get("thresholds"), "weights_sha256": self.weights_sha256 or (meta.get("files", {}).get("model.pt", {}) or {}).get("sha256"),
                "datasets": (meta.get("dataset") or {}).get("datasets"), "training": meta.get("training_summary"),
                "known_limitations": meta.get("known_limitations") or [], "evaluation": ev, "policy": POLICY, "error": self.error,
                "display_tiers": _display_tiers(self.bundle, self.model_id)}


def _display_tiers(bundle: Path, model_id: str) -> dict | None:
    """Plain-language confidence tiers measured on the validation split (scripts/ml/make_display_tiers.py). Display only:
    they never change which detections are stored or the model's operating thresholds."""
    try:
        d = json.loads((bundle / "display_tiers.json").read_text())
    except (OSError, ValueError):
        return None
    return d if d.get("model_id") == model_id else None


def _read_meta(bundle: Path) -> dict:
    try:
        return json.loads((bundle / "metadata.json").read_text())
    except (OSError, ValueError):
        return {}


def _evaluation(bundle: Path) -> dict | None:
    """Headline held-out test metrics recorded in the bundle at packaging time (never recomputed here)."""
    p = bundle / "evaluation_test.json"
    if not p.is_file():
        return None
    try:
        ev = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    m = ev.get("metrics") or {}
    ap, at = m.get("per_class_ap") or {}, m.get("per_class_at_threshold") or {}
    tp, fp, fn = (sum(int(v.get(k) or 0) for v in at.values()) for k in ("tp", "fp", "fn"))
    per = {c: {"AP50": (ap.get(c) or {}).get("AP50"), "AP50_95": (ap.get(c) or {}).get("AP50_95"), "precision": (at.get(c) or {}).get("precision"),
               "recall": (at.get(c) or {}).get("recall"), "gt_instances": (at.get(c) or {}).get("gt_instances")} for c in sorted(set(ap) | set(at))}
    return {"split": ev.get("split", "test"), "subset": ev.get("subset"), "evaluated_at": ev.get("evaluated_at"), "images": m.get("images"),
            "AP50": m.get("AP50"), "AP50_95": m.get("AP50_95"), "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "per_class": per}


_engine: Engine | None = None
_engine_key: tuple | None = None
_engine_lock = threading.Lock()


def engine() -> tuple[Engine | None, str | None]:
    """The process-wide engine for the current settings (or None and the reason)."""
    global _engine, _engine_key
    mid, bundle, why = resolve()
    if mid is None:
        return None, why
    key = (mid, str(bundle))
    with _engine_lock:
        if _engine is None or _engine_key != key:
            _engine, _engine_key = Engine(mid, bundle), key
        return _engine, None


def reset() -> None:
    """Forget the cached engine (tests, or after installing a new bundle)."""
    global _engine, _engine_key, _gate, _gate_key
    with _engine_lock:
        _engine, _engine_key = None, None
        _gate, _gate_key = None, None


def status() -> dict:
    eng, why = engine()
    if eng is None:
        return {"available": False, "reason": why, "policy": POLICY, "domain_gate": gate_status()}
    return {**eng.describe(), "domain_gate": gate_status()}


# ====================================================================== persistence
def registry_model_version(db: Session, eng: Engine, user_id: str) -> ModelVersion:
    """The model_versions row that analysis runs reference (one per bundle, found by its weights checksum).

    model_versions belongs to the legacy training schema (project → dataset version → experiment); a system-level
    registry project (workspace NULL) holds the rows for externally trained bundles so the FK chain stays intact."""
    mv = db.scalar(select(ModelVersion).where(ModelVersion.checksum == eng.weights_sha256, ModelVersion.architecture.like("YOLOX%")).limit(1))
    if mv is not None:
        return mv
    meta = eng.meta
    p = db.scalar(select(Project).where(Project.name == REGISTRY_PROJECT, Project.workspace_id.is_(None), Project.project_type == "object_detection").limit(1))
    if p is None:
        p = Project(name=REGISTRY_PROJECT, description="System registry of packaged detection model bundles. Not a user project.", owner_id=user_id,
                    workspace_id=None, project_type="object_detection", industry="aerospace", model_family="yolox")
        db.add(p); db.flush()
    ds = Dataset(project_id=p.id, name=f"{eng.model_id} training data ({', '.join((meta.get('dataset') or {}).get('datasets') or [])})"[:120])
    db.add(ds); db.flush()
    dv = DatasetVersion(dataset_id=ds.id, project_id=p.id, version=1, seed=int((meta.get("experiment") or {}).get("seed") or 0), val_ratio=0.0, test_ratio=0.0,
                        created_by=user_id)
    db.add(dv); db.flush()
    # the bundle was trained offline (scripts/ml/train_detector.py); this completed "train" job only anchors the FK chain
    job = Job(project_id=p.id, kind="train", status="success", stage="registered", progress=1.0, created_by=user_id,
              message=f"Registered bundle {eng.model_id} (trained offline by scripts/ml/train_detector.py; run {(meta.get('experiment') or {}).get('run_id')})",
              finished_at=now())
    db.add(job); db.flush()
    ex = Experiment(project_id=p.id, job_id=job.id, dataset_version_id=dv.id, seed=dv.seed, status="COMPLETED", created_by=user_id,
                    config={"experiment": meta.get("experiment"), "bundle": eng.model_id})
    db.add(ex); db.flush()
    version = int(MODEL_ID_RE.match(eng.model_id).group(1))
    while db.scalar(select(func.count()).select_from(ModelVersion).where(ModelVersion.project_id == p.id, ModelVersion.version == version)):
        version += 10000                                 # same id, different weights (a re-trained bundle in a test directory)
    mv = ModelVersion(project_id=p.id, experiment_id=ex.id, dataset_version_id=dv.id, version=version, architecture=f"YOLOX-{meta.get('variant')}"[:40],
                      classes=list(meta.get("classes") or []), preprocessing=meta.get("preprocessing") or {}, seed=dv.seed, framework=meta.get("framework") or {},
                      device="cpu", artifact_key=f"models/detection/{eng.model_id}", checksum=eng.weights_sha256, status="COMPLETED",
                      val_metrics={"model_id": eng.model_id, "readiness": meta.get("readiness"), "evaluation": _evaluation(eng.bundle)})
    db.add(mv); db.flush()
    ex.model_version_id = mv.id
    return mv


def next_attempt(db: Session, insp_id: str) -> int:
    return (db.scalar(select(func.max(AnalysisRun.attempt)).where(AnalysisRun.inspection_id == insp_id)) or 0) + 1


def _clip01(v: float) -> float:
    return min(1.0, max(0.0, v))


def persist(db: Session, insp: Inspection, media: InspectionMedia, user_id: str, eng: Engine | None, result: Result | None,
            failure: AnalysisFailed | None, requested_at) -> AnalysisRun | None:
    """Write one analysis run (+ media row + predictions). Returns None when no model row can be referenced
    (the model never loaded): the failure is then recorded on the inspection only."""
    if eng is None or eng.meta is None or eng.weights_sha256 is None:
        return None
    mv = registry_model_version(db, eng, user_id)
    th = eng.meta["thresholds"]
    ts = now()
    run = AnalysisRun(id=uid(), inspection_id=insp.id, workspace_id=insp.workspace_id, attempt=next_attempt(db, insp.id), model_version_id=mv.id,
                      model_checksum=eng.weights_sha256, taxonomy_version=eng.meta.get("taxonomy") or "TAX-1.0",
                      score_threshold=min(th["score_threshold"].values()), iou_threshold=th["nms_iou"],
                      review_threshold=min((th.get("review_threshold") or th["score_threshold"]).values()),
                      status="failed" if failure else ("cancelled" if result and result.out_of_domain else "succeeded"),
                      error_code=failure.code if failure else ("OUT_OF_DOMAIN" if result and result.out_of_domain else None),
                      error_message=str(failure)[:500] if failure else (str((result.gate or {}).get("reason") or "not an aircraft surface")[:500]
                                                                        if result and result.out_of_domain else None),
                      suppressed={**(result.output.get("suppressed") or {} if result else {}), **({"domain_gate": result.gate} if result and result.gate else {}),
                                  **({"out_of_domain": True} if result and result.out_of_domain else {})},
                      timings={**({k: round(v, 2) if isinstance(v, float) else v for k, v in result.timings.items()} if result else {}),
                               "model_id": eng.model_id, "score_threshold_per_class": th["score_threshold"],
                               "review_threshold_per_class": th.get("review_threshold")},
                      requested_by=user_id, requested_at=requested_at, started_at=requested_at, finished_at=ts)
    db.add(run); db.flush()
    if result is None or result.out_of_domain:
        return run
    p, info = result.prep, result.info
    dets = result.output["detections"]
    db.add(AnalysisMedia(analysis_run_id=run.id, media_id=media.id, oriented_width=info["width"], oriented_height=info["height"],
                         input_width=int(p["S_w"]), input_height=int(p["S_h"]), resize_mode=p.get("mode", "letterbox"), scale_x=float(p["r_x"]),
                         scale_y=float(p["r_y"]), pad_x=float(p["pad_x"]), pad_y=float(p["pad_y"]), exif_orientation=int(info.get("orientation") or 1),
                         status="succeeded", n_predictions=len(dets), inference_ms=round(result.timings.get("total_ms", 0.0), 2)))
    k = 0
    for d in dets:
        b = d["box_normalised"]
        x, y = _clip01(b["x"]), _clip01(b["y"])
        w, h = min(_clip01(b["w"]), 1.0 - x), min(_clip01(b["h"]), 1.0 - y)
        if w <= 0 or h <= 0:
            continue
        k += 1
        db.add(DefectPrediction(id=uid(), inspection_id=insp.id, analysis_run_id=run.id, model_version_id=mv.id, media_id=media.id, ordinal=k,
                                class_id=d["class_name"], model_class_index=int(d["class_id"]), taxonomy_version=run.taxonomy_version, score=float(d["score"]),
                                box_x=x, box_y=y, box_w=w, box_h=h, box_input_xyxy=[float(v) for v in d["box_xyxy"]], clipped=bool(d["clipped"]),
                                low_confidence=bool(d["low_confidence"]), predicted_at=ts))
    db.flush()
    return run


def analyse_bytes(data: bytes, ref: str, force: bool = False) -> tuple[Engine | None, Result | None, AnalysisFailed | None]:
    """Domain gate, then the configured detector, on one image. Never raises: failures come back as AnalysisFailed.

    An image the gate judges not to be an aircraft surface gets an out-of-domain Result with no detections (unless
    ``force``: an engineer's explicit, audited override)."""
    eng, why = engine()
    if eng is None:
        return None, None, AnalysisFailed("MODEL_UNAVAILABLE", why or "No detection model is available.")
    try:
        eng.load()
    except AnalysisFailed as ex:
        return eng, None, ex
    verdict = None
    g, gwhy = gate()
    if g is not None:
        verdict = g.check_bytes(data)
        if force:
            verdict = {**verdict, "overridden": True}
        elif not verdict.get("in_domain", True):
            return eng, Result({"detections": [], "suppressed": {}}, None, None, {"gate_ms": verdict.get("ms")}, verdict, True), None
    else:
        verdict = {"state": "not_installed", "reason": gwhy}
    try:
        r = eng.detect(data, ref)
    except AnalysisFailed as ex:
        return eng, None, ex
    r.gate = verdict
    if verdict.get("ms") is not None:
        r.timings["gate_ms"] = verdict["ms"]
    return eng, r, None


# ====================================================================== domain gate
GATE_ID_RE = re.compile(r"^VT-GATE-\d{4}$")


class GateRunner:
    """Process-wide wrapper around visentra_ml.detection.domain_gate.Gate (lazy load, serialised calls)."""

    def __init__(self, gate_id: str, bundle: Path):
        self.gate_id, self.bundle = gate_id, bundle
        self.state, self.error, self.g = "not_loaded", None, None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self.state == "ready" or self.state == "failed":
                return
            try:
                from visentra_ml.detection.domain_gate import Gate
                self.g = Gate(self.bundle, threads=None)
                self.state = "ready"
            except Exception as ex:                       # noqa: BLE001 — a broken gate must not stop inspections
                self.state, self.error = "failed", f"{type(ex).__name__}: {str(ex)[:200]}"
                log.exception("domain gate load failed", extra={"gate_id": self.gate_id})

    def check_bytes(self, data: bytes) -> dict:
        from PIL import Image as PILImage
        self.load()
        if self.state != "ready":
            return {"gate_id": self.gate_id, "state": "failed", "in_domain": True, "reason": "domain gate unavailable; analysed anyway", "error": self.error}
        t0 = time.perf_counter()
        try:
            with self._lock, PILImage.open(io.BytesIO(data)) as im:
                im.load()
                out = self.g.check(im)
        except Exception as ex:                           # noqa: BLE001
            log.exception("domain gate check failed")
            return {"gate_id": self.gate_id, "state": "error", "in_domain": True, "reason": "domain gate error; analysed anyway", "error": type(ex).__name__}
        return {**out, "state": "ready", "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def describe(self) -> dict:
        spec = {}
        try:
            spec = json.loads((self.bundle / "gate.json").read_text())
            ev = json.loads((self.bundle / "evaluation.json").read_text())
        except (OSError, ValueError):
            ev = {}
        return {"gate_id": self.gate_id, "state": self.state, "error": self.error, "threshold": spec.get("threshold"),
                "airplane_min_score": spec.get("airplane_min_score"), "uncertain_band": spec.get("uncertain"), "training_data": spec.get("training_data"),
                "evaluation": {k: ev.get(k) for k in ("valid", "test")} if ev else None}


_gate: GateRunner | None = None
_gate_key: tuple | None = None


def _resolve_gate() -> tuple[str | None, Path | None, str | None]:
    want = (settings.domain_gate or "auto").strip()
    if want.lower() == "off":
        return None, None, "The aircraft-surface check is switched off (VISIONTRA_DOMAIN_GATE=off)."
    root = Path(settings.domain_gate_dir)
    cands = sorted(d for d in root.glob("VT-GATE-*") if GATE_ID_RE.match(d.name) and (d / "gate.json").is_file() and (d / "coco.pth").is_file()) if root.is_dir() else []
    if want.lower() != "auto":
        cands = [d for d in cands if d.name == want]
    if not cands:
        return None, None, "No aircraft-surface check is installed."
    return cands[-1].name, cands[-1], None


def gate() -> tuple[GateRunner | None, str | None]:
    global _gate, _gate_key
    gid, bundle, why = _resolve_gate()
    if gid is None:
        return None, why
    with _engine_lock:
        if _gate is None or _gate_key != (gid, str(bundle)):
            _gate, _gate_key = GateRunner(gid, bundle), (gid, str(bundle))
        return _gate, None


def gate_status() -> dict:
    g, why = gate()
    return g.describe() if g else {"state": "not_installed", "reason": why}


def warm_up_in_background() -> None:
    """Load the detector and the domain gate in a background thread so the first upload does not wait for them."""
    def run():
        try:
            eng, _ = engine()
            if eng:
                eng.load()
            g, _ = gate()
            if g:
                g.load()
        except Exception:                                 # noqa: BLE001 — reported by /health/ready, never fatal
            log.exception("warm-up failed")
    threading.Thread(target=run, name="model-warmup", daemon=True).start()


# ====================================================================== EP-03 / EP-04
RERUN_FROM = ("analysis_failed", "awaiting_review")


def run_out(r: AnalysisRun, mv: ModelVersion | None, n_preds: int) -> dict:
    from .service import iso
    return {"id": r.id, "attempt": r.attempt, "status": r.status, "error_code": r.error_code, "error_message": r.error_message,
            "model": {"model_id": (r.timings or {}).get("model_id"), "version_id": r.model_version_id, "architecture": mv.architecture if mv else None,
                      "weights_sha256": r.model_checksum},
            "thresholds": {"score": r.score_threshold, "iou": r.iou_threshold, "review": r.review_threshold,
                           "score_per_class": (r.timings or {}).get("score_threshold_per_class"),
                           "review_per_class": (r.timings or {}).get("review_threshold_per_class")},
            "predictions": n_preds, "suppressed": r.suppressed, "timings_ms": {k: v for k, v in (r.timings or {}).items() if k.endswith("_ms")},
            "requested_at": iso(r.requested_at), "finished_at": iso(r.finished_at)}


def list_runs(db: Session, user, insp_id: str) -> dict:
    from . import service as SVC
    insp, _ = SVC.inspection_for_user(db, insp_id, user)
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.inspection_id == insp.id).order_by(AnalysisRun.attempt.desc())).all()
    counts = dict(db.execute(select(DefectPrediction.analysis_run_id, func.count()).where(DefectPrediction.inspection_id == insp.id)
                             .group_by(DefectPrediction.analysis_run_id)).all()) if runs else {}
    mvs = {m.id: m for m in db.scalars(select(ModelVersion).where(ModelVersion.id.in_({r.model_version_id for r in runs})))} if runs else {}
    return {"inspection_id": insp.id, "status": insp.status, "items": [run_out(r, mvs.get(r.model_version_id), counts.get(r.id, 0)) for r in runs],
            "model": status()}


def rerun(db: Session, request, user, insp_id: str, force: bool = False, reason: str | None = None) -> tuple[dict, int]:
    """EP-03: analyse the inspection's image again with the configured model (from analysis_failed or awaiting_review)."""
    from .. import errors as E
    from ..audit import audit
    from ..errors import api_error
    from ..security import client_ip
    from ..storage import storage
    from . import lifecycle as L
    from . import service as SVC

    insp, role = SVC.inspection_for_user(db, insp_id, user, "inspection.create")
    expected = SVC.parse_if_match(request) if request.headers.get("if-match") else None
    if insp.status not in RERUN_FROM:
        raise api_error(409, E.CONFLICT, "Analysis can only be run again before the review has started (or after a failed analysis).",
                        current_status=insp.status, allowed_from=list(RERUN_FROM))
    if expected is not None and expected != insp.revision:
        raise api_error(409, E.REVIEW_CONFLICT, "Someone else changed this inspection. Reload to see the latest version.",
                        current_revision=insp.revision, your_revision=expected)
    from . import states as ST
    inherited = False
    if not force and ST.was_overridden(db, insp.id):     # an engineer already overrode the gate for this image: keep it
        force, inherited = True, True
    if force and not inherited:
        reason = (reason or "").strip()
        if len(reason) < 3:
            raise api_error(422, E.VALIDATION, "Give a reason for running detection although the aircraft-surface check rejected the image.", field="reason")
        if len(reason) > 300:
            raise api_error(422, E.VALIDATION, "The reason must be at most 300 characters.", field="reason")
    eng, why = engine()
    if eng is None:
        raise api_error(503, E.MODEL_UNAVAILABLE, why or "No detection model is available.")
    media = db.scalars(select(InspectionMedia).where(InspectionMedia.inspection_id == insp.id).order_by(InspectionMedia.ordinal)).first()
    if media is None:
        raise api_error(409, E.CONFLICT, "This inspection has no image to analyse.")
    try:
        data = storage.get(media.object_key)
    except Exception:                                    # noqa: BLE001
        raise api_error(503, E.STORAGE_UNAVAILABLE, "The original image could not be read from the image store.") from None
    import hashlib
    if hashlib.sha256(data).hexdigest() != media.sha256:
        raise api_error(409, "MEDIA_INTEGRITY", "The stored original no longer matches its recorded checksum; it was not analysed.")
    requested_at = now()
    rev0 = insp.revision
    eng, result, failure = analyse_bytes(data, media.filename, force=force)
    if result is not None and result.gate and force:
        result.gate = {**result.gate, "overridden": True, "override_by": user.id, "override_reason": reason if not inherited else "kept from an earlier override",
                       "override_inherited": inherited}

    def tx():
        L.transition(db, insp.id, "analysing", expected_revision=rev0, fields={"analysis_mode": "model"})
        to = "analysis_failed" if failure else "awaiting_review"
        L.transition(db, insp.id, to, error_code=failure.code if failure else None)
        fresh = db.get(Inspection, insp.id)
        db.refresh(fresh)
        run = persist(db, fresh, media, user.id, eng, result, failure, requested_at)
        audit(db, "inspection.analyse", "inspection", insp.id, user_id=user.id, result="failure" if failure else "success", ip=client_ip(request),
              meta={"model_id": eng.model_id, "analysis_run_id": run.id if run else None, "error": failure.code if failure else None,
                    "predictions": len(result.output["detections"]) if result else 0, "forced": force, "override_reason": reason if force else None,
                    "override_inherited": inherited,
                    "out_of_domain": bool(result and result.out_of_domain)})
        db.commit()
        db.refresh(fresh)
        return fresh

    fresh = SVC.write_retry(db, tx)
    body = SVC.full_out(db, fresh, role)
    if failure:
        body["analysis_note"] = f"Detection failed ({failure.code}): {failure}."
    return body, fresh.revision


def out_of_domain_note(result: Result | None) -> str | None:
    if result is None or not result.out_of_domain:
        return None
    return (f"This image does not look like an aircraft surface ({(result.gate or {}).get('reason', 'domain check')}). "
            "No defect detection was run. If it is an aircraft surface, run detection anyway.")
