"""Detection training/evaluation configuration: loading, path safety and validation.

A configuration is a JSON file (schema ``visiontra.detection_experiment/1``). Every experiment
setting lives there, so experiments never require code changes. ``validate`` returns a list of
human-readable problems; an empty list means the configuration is structurally valid. Data checks
(files readable, labels valid) are done separately by ``data.readiness``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "visiontra.detection_experiment/1"
ROOT = Path(__file__).resolve().parents[3]          # repository root
EXP_ID = re.compile(r"^VT-EXP-\d{3}$")
SPLITS = ("train", "valid", "test")
SUBSETS = ("all", "eval_representatives", "one_file_per_asset")
ALLOWED_MAPPING_STATUSES = {"ACCEPTED", "PROVISIONAL_ACCEPTED"}
VARIANTS = {  # YOLOX family: depth, width, depthwise convs
    "nano": (0.33, 0.25, True), "tiny": (0.33, 0.375, False), "s": (0.33, 0.50, False), "m": (0.67, 0.75, False),
}


class ConfigError(ValueError):
    pass


def resolve(rel: str, *, must_exist: bool = False, writable: bool = False) -> Path:
    """Resolve a repository-relative path and refuse anything outside the repository.

    Writable paths must not point into ``datasets/`` (raw data and manifests are never written by training).
    """
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        raise ConfigError(f"path must be repository-relative: {rel!r}")
    p = (ROOT / rel).resolve()
    if ROOT != p and ROOT not in p.parents:
        raise ConfigError(f"path escapes the repository: {rel!r}")
    if writable and (ROOT / "datasets") in [p, *p.parents]:
        raise ConfigError(f"outputs must not be written under datasets/: {rel!r}")
    if must_exist and not p.exists():
        raise ConfigError(f"path does not exist: {rel!r}")
    return p


def load(path: str | Path) -> dict:
    p = Path(path)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"configuration not found: {p}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"configuration is not valid JSON: {p}: {e}") from e
    problems = validate(cfg)
    if problems:
        raise ConfigError("invalid configuration:\n- " + "\n- ".join(problems))
    return cfg


def digest(cfg: dict) -> str:
    """Stable sha256 of a configuration (sorted keys, no whitespace)."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _num(d, key, lo, hi, problems, where, integer=False):
    v = d.get(key)
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi and (not integer or float(v).is_integer())
    if not ok:
        problems.append(f"{where}.{key} must be {'an integer' if integer else 'a number'} in [{lo}, {hi}] (got {v!r})")
    return v


def validate(cfg: dict, *, check_paths: bool = True) -> list[str]:
    """Structural validation. Returns a list of problems (empty = valid)."""
    P: list[str] = []
    if not isinstance(cfg, dict):
        return ["configuration must be a JSON object"]
    if cfg.get("schema") != SCHEMA:
        P.append(f"schema must be {SCHEMA!r}")
    if not EXP_ID.match(str(cfg.get("experiment_id", ""))):
        P.append("experiment_id must look like VT-EXP-001")
    ds, m, tr, aug, ev = (cfg.get(k) for k in ("dataset", "model", "train", "augment", "eval"))
    for name, sec in (("dataset", ds), ("model", m), ("train", tr), ("augment", aug), ("eval", ev)):
        if not isinstance(sec, dict):
            P.append(f"section '{name}' is missing")
    if P:
        return P
    # dataset
    if not re.match(r"^VT-PREP-\d{3}$", str(ds.get("prep_id", ""))):
        P.append("dataset.prep_id must look like VT-PREP-001")
    if not ds.get("datasets") or not all(re.match(r"^VT-DS-\d{3}$", str(x)) for x in ds["datasets"]):
        P.append("dataset.datasets must list VT-DS-### ids")
    sp = ds.get("splits") or {}
    for s in SPLITS:
        if s not in sp:
            P.append(f"dataset.splits.{s} is missing")
        elif check_paths:
            try:
                resolve(sp[s], must_exist=True)
            except ConfigError as e:
                P.append(f"dataset.splits.{s}: {e}")
    if check_paths:
        try:
            resolve(ds.get("class_mapping", ""), must_exist=True)
        except ConfigError as e:
            P.append(f"dataset.class_mapping: {e}")
    tc = ds.get("target_classes")
    if not isinstance(tc, list) or not tc or len(set(tc)) != len(tc):
        P.append("dataset.target_classes must be a non-empty list of unique class names")
    st = set(ds.get("mapping_statuses_allowed") or [])
    if not st or not st <= ALLOWED_MAPPING_STATUSES:
        P.append(f"dataset.mapping_statuses_allowed must be a subset of {sorted(ALLOWED_MAPPING_STATUSES)} (UNRESOLVED/NOT_MAPPED can never be used)")
    if ds.get("train_sampling") not in ("all_files", "one_file_per_asset_per_epoch"):
        P.append("dataset.train_sampling must be 'all_files' or 'one_file_per_asset_per_epoch'")
    for s in ("valid_subset", "test_subset"):
        if ds.get(s) not in SUBSETS:
            P.append(f"dataset.{s} must be one of {SUBSETS}")
    # model
    if m.get("family") != "YOLOX":
        P.append("model.family must be 'YOLOX' (the only implementation wired in)")
    if m.get("variant") not in VARIANTS:
        P.append(f"model.variant must be one of {sorted(VARIANTS)}")
    size = m.get("input_size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(x, int) and 128 <= x <= 1280 and x % 32 == 0 for x in size)):
        P.append("model.input_size must be [height, width], multiples of 32 between 128 and 1280")
    pre = m.get("pretrained")
    if pre is not None:
        if not isinstance(pre, dict) or not re.match(r"^[0-9a-f]{64}$", str(pre.get("sha256", ""))) or not pre.get("licence") or not pre.get("source_url"):
            P.append("model.pretrained needs path, sha256 (64 hex), licence and source_url")
    # train
    _num(tr, "epochs", 1, 300, P, "train", integer=True)
    _num(tr, "batch_size", 1, 128, P, "train", integer=True)
    _num(tr, "seed", 0, 2**31 - 1, P, "train", integer=True)
    _num(tr, "num_threads", 1, 64, P, "train", integer=True)
    if tr.get("grad_clip_norm") is not None:
        _num(tr, "grad_clip_norm", 0.1, 1000, P, "train")
    if tr.get("device") not in ("cpu", "cuda", "auto"):
        P.append("train.device must be cpu, cuda or auto")
    opt = tr.get("optimizer") or {}
    if opt.get("name") != "sgd":
        P.append("train.optimizer.name must be 'sgd'")
    else:
        _num(opt, "lr_per_img", 1e-6, 1e-2, P, "train.optimizer")
        _num(opt, "momentum", 0, 0.999, P, "train.optimizer")
        _num(opt, "weight_decay", 0, 0.1, P, "train.optimizer")
    sch = tr.get("scheduler") or {}
    if sch.get("name") != "cosine":
        P.append("train.scheduler.name must be 'cosine'")
    else:
        _num(sch, "warmup_epochs", 0, 10, P, "train.scheduler")
        _num(sch, "min_lr_ratio", 0, 1, P, "train.scheduler")
    es = tr.get("early_stopping") or {}
    if es.get("metric") not in ("AP50", "AP50_95"):
        P.append("train.early_stopping.metric must be AP50 or AP50_95 (computed on the validation split)")
    _num(es, "patience", 1, 100, P, "train.early_stopping", integer=True)
    if tr.get("selection_split", "valid") != "valid":
        P.append("train.selection_split must be 'valid' — the test split is never used for model selection")
    # augment (AUG-01/02, Phase 7 §8)
    _num(aug, "hflip", 0, 1, P, "augment")
    _num(aug, "vflip", 0, 1, P, "augment")
    sc = aug.get("scale")
    if not (isinstance(sc, list) and len(sc) == 2 and 0.5 <= sc[0] <= 1 <= sc[1] <= 1.5):
        P.append("augment.scale must be [lo, hi] with 0.5 ≤ lo ≤ 1 ≤ hi ≤ 1.5")
    _num(aug, "translate", 0, 0.25, P, "augment")
    hsv = aug.get("hsv") or {}
    _num(hsv, "h", 0, 0.02, P, "augment.hsv")    # strong hue shifts change defect appearance (AUG-02)
    _num(hsv, "s", 0, 0.5, P, "augment.hsv")
    _num(hsv, "v", 0, 0.5, P, "augment.hsv")
    ph = aug.get("photometric")
    if ph is not None:                                   # optional (added for VT-EXP-005); older configs have none
        for k in ("blur", "noise", "jpeg", "gray", "contrast"):
            _num(ph if isinstance(ph, dict) else {}, k, 0, 0.6, P, "augment.photometric")
    bg = (ds or {}).get("background") or {}
    if bg:
        if not isinstance(bg.get("manifests"), list) or not bg["manifests"]:
            P.append("dataset.background.manifests must be a non-empty list")
        _num(bg, "per_epoch", 0, 5000, P, "dataset.background", integer=True)
    for k in ("mosaic", "mixup"):
        if aug.get(k, 0) != 0:
            P.append(f"augment.{k} is not supported: it pastes content over annotated defects (AUG-02)")
    if aug.get("vflip", 0) > 0 and not aug.get("vflip_justification"):
        P.append("augment.vflip > 0 needs augment.vflip_justification")
    # eval
    _num(ev, "nms_iou", 0.1, 0.95, P, "eval")
    _num(ev, "score_floor", 0, 0.5, P, "eval")
    _num(ev, "max_det", 1, 1000, P, "eval", integer=True)
    _num(ev, "match_iou", 0.1, 0.95, P, "eval")
    # output
    try:
        out = resolve(cfg.get("output_dir", ""), writable=True)
        if not str(out.relative_to(ROOT)).startswith("experiments"):
            P.append("output_dir must be under experiments/")
    except ConfigError as e:
        P.append(f"output_dir: {e}")
    return P
