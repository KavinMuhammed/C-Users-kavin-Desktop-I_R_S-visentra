"""Model artefact packaging, verification and loading.

Bundle layout (immutable, one directory per model version)::

    models/detection/<model_id>/
      model.pt            {"format": "visiontra-detector/1", "state_dict": …, "meta": {…}}  (torch_state_dict)
      metadata.json       the same "meta" plus file checksums and sizes
      SHA256SUMS          sha256 of every other file in the bundle
      evaluation.json     copied from the experiment's final evaluation (if any)
      config.json         training configuration snapshot
      environment.json    training environment snapshot

``model.pt`` loads with ``torch.load(weights_only=True)``: no pickled code is executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

FORMAT = "visiontra-detector/1"
MODEL_ID = re.compile(r"^VT-DET-\d{4}$")
READINESS = ("DATA_BLOCKED", "EXPERIMENTAL", "EVALUATED_LIMITED", "CANDIDATE_FOR_INTEGRATION")
REQUIRED_META = ("model_id", "version", "task", "architecture", "variant", "framework", "classes", "supported_classes", "experimental_classes",
                 "input_size", "preprocessing", "taxonomy", "class_mapping", "dataset", "experiment", "thresholds", "weights_licence",
                 "pretrained", "readiness", "known_limitations")


class ArtifactError(ValueError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check_meta(meta: dict) -> list[str]:
    P = [f"metadata field missing: {k}" for k in REQUIRED_META if k not in meta]
    if P:
        return P
    if not MODEL_ID.match(meta["model_id"]):
        P.append("model_id must look like VT-DET-0001")
    if meta["task"] != "detection":
        P.append("task must be 'detection'")
    if meta["readiness"] not in READINESS:
        P.append(f"readiness must be one of {READINESS}")
    cls = meta["classes"]
    if not (set(meta["supported_classes"]) | set(meta["experimental_classes"])) <= set(cls):
        P.append("supported/experimental classes must be model classes")
    if set(cls) - set(meta["supported_classes"]) - set(meta["experimental_classes"]):
        P.append("every model class must be declared supported or experimental")
    if set(meta["supported_classes"]) & set(meta["experimental_classes"]):
        P.append("a class cannot be both supported and experimental")
    if meta["readiness"] in ("DATA_BLOCKED", "EXPERIMENTAL") and meta["supported_classes"]:
        P.append(f"a {meta['readiness']} model cannot declare supported classes")
    for k in ("score_threshold", "review_threshold"):
        th = meta["thresholds"].get(k)
        if th is not None and set(th) != set(cls):
            P.append(f"thresholds.{k} must have one value per class")
    return P


def package(model_id: str, state_dict: dict, meta: dict, *, out_root: Path, extra_files: dict[str, Path] | None = None) -> Path:
    """Write a new bundle. Refuses to overwrite an existing model version."""
    import torch

    meta = json.loads(json.dumps({**meta, "model_id": model_id, "format": FORMAT}, default=str))
    problems = check_meta(meta)
    if problems:
        raise ArtifactError("; ".join(problems))
    d = out_root / model_id
    if d.exists():
        raise ArtifactError(f"{d} already exists — model versions are immutable; use a new model_id")
    tmp = out_root / f".{model_id}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    torch.save({"format": FORMAT, "state_dict": state_dict, "meta": meta}, tmp / "model.pt")
    for name, src in (extra_files or {}).items():
        shutil.copyfile(src, tmp / name)
    files = sorted(p.name for p in tmp.iterdir())
    sums = {n: sha256(tmp / n) for n in files}
    (tmp / "metadata.json").write_text(json.dumps({**meta, "files": {n: {"sha256": sums[n], "bytes": (tmp / n).stat().st_size} for n in files}}, indent=1))
    sums["metadata.json"] = sha256(tmp / "metadata.json")
    (tmp / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in sorted(sums.items())))
    os.replace(tmp, d)
    return d


def verify(bundle: Path) -> dict:
    """Check every checksum and the metadata. Does not import torch."""
    sums_path = bundle / "SHA256SUMS"
    if not sums_path.is_file():
        raise ArtifactError("SHA256SUMS missing")
    bad = []
    for line in sums_path.read_text().splitlines():
        h, name = line.split("  ", 1)
        p = bundle / name
        if not p.is_file():
            bad.append(f"{name}: missing")
        elif sha256(p) != h:
            bad.append(f"{name}: checksum mismatch")
    meta = json.loads((bundle / "metadata.json").read_text())
    bad += check_meta(meta)
    if meta.get("files", {}).get("model.pt", {}).get("sha256") != sha256(bundle / "model.pt"):
        bad.append("model.pt checksum differs from metadata.json")
    return {"ok": not bad, "problems": bad, "meta": meta}


def load(bundle: Path, *, verify_first: bool = True):
    """Load a bundle into a ready-to-run model (eval mode). Raises ArtifactError on any mismatch."""
    import torch

    from . import model as Mo

    if verify_first:
        v = verify(bundle)
        if not v["ok"]:
            raise ArtifactError("; ".join(v["problems"]))
    blob = torch.load(bundle / "model.pt", map_location="cpu", weights_only=True)
    if blob.get("format") != FORMAT:
        raise ArtifactError("unrecognised model format")
    meta = blob["meta"]
    m = Mo.build(meta["variant"], len(meta["classes"]))
    m.load_state_dict(blob["state_dict"], strict=True)
    m.eval()
    return m, meta
