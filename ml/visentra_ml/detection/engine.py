"""Detection training and evaluation engine (YOLOX on PyTorch).

A run lives in ``<output_dir>/<run_id>/`` and is never overwritten:

    run.json            status record: planned → running → completed | failed | interrupted (+ resumed)
    config.json         exact configuration used (and its sha256 in run.json)
    environment.json    software, hardware and source revision
    history.jsonl       one line per epoch: losses, learning rate, validation metrics, timings
    checkpoints/last.pt resume state (model, EMA, optimiser, epoch, RNG) — written atomically
    checkpoints/best.pt EMA weights of the epoch with the best validation metric (state dict only)
    train.log           plain-text log

The test split is never read during training.
"""
from __future__ import annotations

import json
import math
import os
import platform
import random
import signal
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from . import config as C
from . import data as Dt
from . import metrics as M
from . import model as Mo

utc = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731


def git_revision() -> dict:
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=C.ROOT, capture_output=True, text=True, timeout=10).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "ml", "scripts/ml"], cwd=C.ROOT, capture_output=True, text=True, timeout=10).stdout.strip())
        return {"commit": rev, "ml_code_dirty": dirty}
    except Exception:  # noqa: BLE001
        return {"commit": None, "ml_code_dirty": None}


def environment(device: str) -> dict:
    import PIL
    import torchvision
    try:
        import pycocotools  # noqa: F401
        from importlib.metadata import version
        coco = version("pycocotools")
    except Exception:  # noqa: BLE001
        coco = None
    return {"python": platform.python_version(), "torch": torch.__version__, "torchvision": torchvision.__version__, "numpy": np.__version__,
            "pillow": PIL.__version__, "pycocotools": coco, "yolox": "vendored 0.3.0 @ 41977848 (model code only)",
            "platform": platform.platform(), "cpu": platform.processor() or platform.machine(), "cpu_count": os.cpu_count(),
            "device": device, "cuda": torch.version.cuda if torch.cuda.is_available() else None, "torch_threads": torch.get_num_threads(),
            "source": git_revision()}


def set_seed(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


class EMA:
    """Exponential moving average of weights (YOLOX ModelEMA: decay 0.9998 ramped over ~2000 updates)."""

    def __init__(self, model, decay=0.9998, updates=0):
        self.ema = deepcopy(model).eval()
        self.updates, self.decay = updates, lambda x: decay * (1 - math.exp(-x / 2000))
        for p in self.ema.parameters():
            p.requires_grad_(False)

    @torch.no_grad()
    def update(self, model):
        self.updates += 1
        d = self.decay(self.updates)
        msd = model.state_dict()
        for k, v in self.ema.state_dict().items():
            if v.dtype.is_floating_point:
                v.mul_(d).add_(msd[k].detach(), alpha=1 - d)


def param_groups(model, wd):
    bn, w, b = [], [], []
    for mod in model.modules():
        if hasattr(mod, "bias") and isinstance(mod.bias, torch.nn.Parameter):
            b.append(mod.bias)
        if isinstance(mod, torch.nn.BatchNorm2d):
            bn.append(mod.weight)
        elif hasattr(mod, "weight") and isinstance(mod.weight, torch.nn.Parameter):
            w.append(mod.weight)
    return [{"params": bn, "weight_decay": 0.0}, {"params": w, "weight_decay": wd}, {"params": b, "weight_decay": 0.0}]


def lr_at(it: int, total: int, warm: int, base: float, min_ratio: float) -> float:
    """YOLOX 'warmcos': quadratic warm-up, then cosine decay to base·min_ratio."""
    if warm and it < warm:
        return base * (it / warm) ** 2
    t = (it - warm) / max(1, total - warm)
    return base * min_ratio + 0.5 * base * (1 - min_ratio) * (1 + math.cos(math.pi * min(1.0, t)))


# ------------------------------------------------------------------ inference over items
@torch.no_grad()
def predict_items(model, items, size, ev: dict, *, batch: int = 8, num_classes: int = 1, progress=None):
    """Returns (gts, preds, sizes) in oriented-image pixels, keyed by file_id."""
    model.eval()
    dev = next(model.parameters()).device                     # the model may live on the GPU; inputs must follow it
    ds = Dt.DetectionDataset(items, tuple(size), augment=None)
    gts, preds, sizes = {}, {}, {}
    for s in range(0, len(ds), batch):
        chunk = [ds[i] for i in range(s, min(len(ds), s + batch))]
        imgs, _, metas = Dt.collate(chunk)
        raw = model(imgs.to(dev)).float().cpu()
        dets = Mo.postprocess(raw, num_classes, score_floor=ev["score_floor"], nms_iou=ev["nms_iou"], max_det=ev["max_det"])
        for d, meta in zip(dets, metas):
            it = items[meta["index"]]
            W, H = meta["orig_w"], meta["orig_h"]
            sizes[it.file_id] = (W, H)
            gts[it.file_id] = [(b.cls, [b.x1 * W, b.y1 * H, b.x2 * W, b.y2 * H]) for b in it.boxes]
            preds[it.file_id] = [(p["cls"], p["score"], p["xyxy"]) for p in Mo.to_original(d, meta)]
        if progress:
            progress(min(len(ds), s + batch), len(ds))
    return gts, preds, sizes


def evaluate_preds(gts, preds, sizes, classes, *, match_iou: float, score_threshold) -> dict:
    ap = M.coco_ap(gts, preds, classes, sizes)
    at = M.counts_at(gts, preds, classes, iou_thr=match_iou, score_thr=score_threshold)
    agg = {"AP50": float(np.mean([ap[c]["AP50"] for c in classes if ap[c]["AP50"] is not None])) if any(ap[c]["AP50"] is not None for c in classes) else None,
           "AP50_95": float(np.mean([ap[c]["AP50_95"] for c in classes if ap[c]["AP50_95"] is not None])) if any(ap[c]["AP50_95"] is not None for c in classes) else None}
    return {"per_class_ap": ap, "per_class_at_threshold": at, **agg, "images": len(sizes),
            "images_with_gt": sum(1 for v in gts.values() if v), "predictions": sum(len(v) for v in preds.values())}


# ------------------------------------------------------------------ run records
class Run:
    def __init__(self, cfg: dict, run_dir: Path, *, resume: bool = False):
        self.cfg, self.dir = cfg, run_dir
        self.ck = run_dir / "checkpoints"
        if resume:
            self.rec = json.loads((run_dir / "run.json").read_text())
        else:
            run_dir.mkdir(parents=True, exist_ok=False)          # never overwrite an earlier run
            self.ck.mkdir()
            self.rec = {"schema": "visiontra.experiment_run/1", "experiment_id": cfg["experiment_id"], "run_id": run_dir.name,
                        "created_at": utc(), "status": "planned", "config_sha256": C.digest(cfg), "events": []}
            (run_dir / "config.json").write_text(json.dumps(cfg, indent=1))
            self.save()

    def save(self):
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(json.dumps(self.rec, indent=1, default=str)); os.replace(tmp, self.dir / "run.json")

    def event(self, kind, **kw):
        self.rec["events"].append({"at": utc(), "event": kind, **kw}); self.save()
        self.log(f"{kind} {json.dumps(kw, default=str)}")

    def log(self, msg):
        with open(self.dir / "train.log", "a", encoding="utf-8") as f:
            f.write(f"{utc()} {msg}\n")

    def history(self, row):
        with open(self.dir / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def _atomic_save(obj, path: Path):
    tmp = path.with_suffix(".tmp"); torch.save(obj, tmp); os.replace(tmp, path)


# ------------------------------------------------------------------ training
def train(cfg: dict, *, run_dir: Path | None = None, resume: bool = False, smoke: dict | None = None, echo=print) -> dict:
    """Train according to ``cfg``. ``smoke`` = {"train": n, "valid": n, "epochs": n} limits sizes for a pipeline check."""
    tr, ev, ds = cfg["train"], cfg["eval"], cfg["dataset"]
    out_root = C.resolve(cfg["output_dir"], writable=True)
    if run_dir is None:
        run_dir = out_root / f"{cfg['experiment_id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{'-smoke' if smoke else ''}"
    run = Run(cfg, run_dir, resume=resume)
    stop = {"flag": None}

    def _sig(signum, _frame):
        stop["flag"] = signal.Signals(signum).name
    old = {s: signal.signal(s, _sig) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        torch.set_num_threads(tr["num_threads"])
        device = "cuda" if tr["device"] == "cuda" or (tr["device"] == "auto" and torch.cuda.is_available()) else "cpu"
        if not resume:
            (run.dir / "environment.json").write_text(json.dumps(environment(device), indent=1))
        set_seed(tr["seed"])
        mapping = Dt.load_mapping(cfg)
        classes = mapping["targets"]
        train_items, _ = Dt.load_items(cfg, "train", mapping, subset="all")
        bg_items = Dt.load_background(cfg, "train")
        bg_per_epoch = int(((cfg["dataset"].get("background") or {}).get("per_epoch")) or 0)
        valid_items, _ = Dt.load_items(cfg, "valid", mapping, subset=ds["valid_subset"])
        if smoke:
            rs = random.Random(tr["seed"])
            train_items = rs.sample(train_items, min(smoke["train"], len(train_items)))
            valid_items = rs.sample(valid_items, min(smoke["valid"], len(valid_items)))
        # nose-damage recovery: reviewed hard cases (train split only) and scale-aware oversampling of photo groups
        # that show a recognisable aircraft with dents (both multiply an item's appearances per epoch)
        hc_cfg = ds.get("hard_cases") or {}
        hc_items = Dt.load_hard_cases(cfg, "train", classes) if hc_cfg else []
        n_plain = len(train_items)
        train_items = train_items + hc_items
        boost_groups = set()
        os_cfg = ds.get("oversample") or {}
        if os_cfg.get("groups_file"):
            boost_groups = set(json.loads(C.resolve(os_cfg["groups_file"], must_exist=True).read_text())["groups"])
        mult = [int(hc_cfg.get("repeat", 1)) if i >= n_plain
                else (int(os_cfg.get("repeat", 1)) if f"{it.dataset_id}|{it.photo_group}" in boost_groups else 1)
                for i, it in enumerate(train_items)]
        epochs = smoke["epochs"] if smoke else tr["epochs"]
        run.rec.update({"status": "running", "smoke": bool(smoke), "device": device, "classes": classes,
                        "mapping": {k: mapping[k] for k in ("mapping_id", "version", "taxonomy", "sha256")},
                        "data": {"prep_id": ds["prep_id"], "datasets": ds["datasets"], "train": Dt.summarise(train_items, classes),
                                 "valid": Dt.summarise(valid_items, classes), "valid_subset": ds["valid_subset"], "train_sampling": ds["train_sampling"],
                                 "background": {"images": len(bg_items), "per_epoch": min(bg_per_epoch, len(bg_items)),
                                                "manifests": (cfg["dataset"].get("background") or {}).get("manifests", [])},
                                 "split_files": ds["splits"],
                                 "hard_cases": {"images": len(hc_items), "boxes": sum(len(i.boxes) for i in hc_items), "repeat": int(hc_cfg.get("repeat", 1)),
                                                "annotations": hc_cfg.get("annotations")},
                                 "oversample": {"groups": len(boost_groups), "repeat": int(os_cfg.get("repeat", 1)),
                                                "files_boosted": sum(1 for m in mult[:n_plain] if m > 1)}},
                        "epochs_planned": epochs})
        run.rec.setdefault("started_at", utc())
        if resume:
            run.rec["status"] = "running"; run.event("resumed")
        else:
            run.event("started", device=device, train_images=len(train_items), valid_images=len(valid_items))

        model = Mo.build(cfg["model"]["variant"], len(classes)).to(device)
        if cfg["model"].get("pretrained") and not resume:
            info = Mo.load_pretrained(model, cfg["model"]["pretrained"])
            run.rec["pretrained"] = {**{k: cfg["model"]["pretrained"][k] for k in ("source_url", "licence", "sha256")}, "loaded_tensors": info["loaded_tensors"],
                                     "skipped_shape_mismatch": info["skipped_shape_mismatch"], "not_in_checkpoint": info["not_in_checkpoint"]}
            run.save()
        opt_cfg = tr["optimizer"]
        base_lr = opt_cfg["lr_per_img"] * tr["batch_size"]
        opt = torch.optim.SGD(param_groups(model, opt_cfg["weight_decay"]), lr=base_lr, momentum=opt_cfg["momentum"], nesterov=True)
        ema = EMA(model) if tr.get("ema", True) else None
        start_epoch, best, bad_epochs, it_global = 0, {"value": None, "epoch": None}, 0, 0
        if resume:
            st = torch.load(run.ck / "last.pt", map_location="cpu", weights_only=True)
            model.load_state_dict(st["model"]); opt.load_state_dict(st["optimizer"])
            if ema and st.get("ema"):
                ema.ema.load_state_dict(st["ema"]); ema.updates = st["ema_updates"]
            start_epoch, best, bad_epochs, it_global = st["epoch"] + 1, st["best"], st["bad_epochs"], st["iteration"]
            random.setstate(tuple(st["py_rng"]) if isinstance(st["py_rng"], list) else st["py_rng"])
            torch.set_rng_state(st["torch_rng"])
        size = cfg["model"]["input_size"]
        n_def = len(train_items)
        dset = Dt.DetectionDataset(train_items + bg_items, tuple(size), augment=cfg["augment"], seed=tr["seed"])
        bg_rng = random.Random(tr["seed"] + 11)
        idx_rng = random.Random(tr["seed"] + 7)
        per_epoch = sum(mult[i] for i in Dt.epoch_indices(train_items, ds["train_sampling"], random.Random(0))) + min(bg_per_epoch, len(bg_items))
        iters_per_epoch = math.ceil(per_epoch / tr["batch_size"])
        total_iters = iters_per_epoch * epochs
        warm = int(tr["scheduler"]["warmup_epochs"] * iters_per_epoch)
        metric = tr["early_stopping"]["metric"]
        def epoch_order():
            order = Dt.epoch_indices(train_items, ds["train_sampling"], idx_rng)
            order += [i for i in order for _ in range(mult[i] - 1)]
            idx_rng.shuffle(order)
            if bg_items and bg_per_epoch:
                order += [n_def + i for i in bg_rng.sample(range(len(bg_items)), min(bg_per_epoch, len(bg_items)))]
                bg_rng.shuffle(order)
            return order
        for _ in range(start_epoch):            # replay the index RNGs so a resumed run sees the same order
            epoch_order()
        n_threads = int(tr.get("loader_threads") or max(1, min(8, (os.cpu_count() or 2) - 1)))
        prefetch = max(2, n_threads)
        pool = ThreadPoolExecutor(max_workers=n_threads, thread_name_prefix="vt-load")
        run.rec["loader_threads"] = n_threads
        for epoch in range(start_epoch, epochs):
            t0 = time.time(); model.train(); dset.reseed(tr["seed"] * 1000 + epoch)
            order = epoch_order()
            sums = {"total_loss": 0.0, "iou_loss": 0.0, "conf_loss": 0.0, "cls_loss": 0.0}; nb = 0; gnorm_sum = 0.0; gnorm_max = 0.0
            # batches are decoded/augmented on worker threads (PIL and numpy release the GIL) a few batches ahead of the
            # model, each sample with its own seed, so the GPU is not left waiting on JPEG decoding
            bs = tr["batch_size"]; eseed = tr["seed"] * 1000 + epoch
            starts = list(range(0, len(order), bs))
            def load_batch(b0):
                return Dt.collate([dset.sample(i, eseed * 1_000_003 + b0 + k) for k, i in enumerate(order[b0:b0 + bs])])
            pending = [pool.submit(load_batch, b0) for b0 in starts[:prefetch]]
            for bi, b in enumerate(starts):
                if stop["flag"]:
                    for f in pending:
                        f.cancel()
                    break
                imgs, targets, _ = pending.pop(0).result()
                if bi + prefetch < len(starts):
                    pending.append(pool.submit(load_batch, starts[bi + prefetch]))
                lr = lr_at(it_global, total_iters, warm, base_lr, tr["scheduler"]["min_lr_ratio"])
                for g in opt.param_groups:
                    g["lr"] = lr
                out = model(imgs.to(device), targets.to(device))
                loss = out["total_loss"]
                if not torch.isfinite(loss):
                    raise FloatingPointError(f"non-finite loss at epoch {epoch} iteration {it_global}")
                opt.zero_grad(); loss.backward()
                if tr.get("grad_clip_norm"):
                    gn = float(torch.nn.utils.clip_grad_norm_(model.parameters(), tr["grad_clip_norm"]))
                    gnorm_sum += gn; gnorm_max = max(gnorm_max, gn)
                opt.step()
                if ema:
                    ema.update(model)
                for k in sums:
                    sums[k] += float(out[k].detach()) if torch.is_tensor(out[k]) else float(out[k])
                nb += 1; it_global += 1
                if nb % 20 == 0:
                    run.log(f"epoch {epoch} batch {nb}/{iters_per_epoch} loss {sums['total_loss'] / nb:.4f} lr {lr:.5f}")
            if stop["flag"]:
                _atomic_save(_state(model, opt, ema, epoch - 1, best, bad_epochs, it_global - nb), run.ck / "last.pt")
                run.rec["status"] = "interrupted"; run.event("interrupted", signal=stop["flag"], epoch=epoch, note="resume with --resume; the partial epoch is repeated")
                return run.rec
            train_s = time.time() - t0
            emodel = ema.ema if ema else model
            row = {"epoch": epoch, "lr_end": lr, "train_batches": nb, "train_images": len(order), "train_seconds": round(train_s, 1),
                   **{k: v / max(1, nb) for k, v in sums.items()},
                   **({"grad_norm_mean_before_clip": gnorm_sum / max(1, nb), "grad_norm_max_before_clip": gnorm_max} if tr.get("grad_clip_norm") else {})}
            if (epoch + 1) % tr.get("eval_every", 1) == 0 or epoch == epochs - 1:
                t1 = time.time()
                g, p, s = predict_items(emodel, valid_items, size, ev, num_classes=len(classes))
                vm = evaluate_preds(g, p, s, classes, match_iou=ev["match_iou"], score_threshold=ev.get("report_threshold", 0.25))
                row.update({"valid_" + k: vm[k] for k in ("AP50", "AP50_95")}, valid_per_class=vm["per_class_at_threshold"], valid_seconds=round(time.time() - t1, 1))
                val = vm[metric]
                improved = val is not None and (best["value"] is None or val > best["value"])
                if improved:
                    best = {"value": val, "epoch": epoch, "metric": metric}; bad_epochs = 0
                    _atomic_save({"format": "visiontra-detector-ckpt/1", "state_dict": emodel.state_dict(), "epoch": epoch, "valid": {k: vm[k] for k in ("AP50", "AP50_95")}}, run.ck / "best.pt")
                else:
                    bad_epochs += 1
                row["best_so_far"] = best
            run.history(row); echo(json.dumps({k: v for k, v in row.items() if k != "valid_per_class"}, default=str))
            _atomic_save(_state(model, opt, ema, epoch, best, bad_epochs, it_global), run.ck / "last.pt")
            run.rec.update({"epochs_completed": epoch + 1, "best": best}); run.save()
            if bad_epochs >= tr["early_stopping"]["patience"]:
                run.event("early_stopped", epoch=epoch, best=best); break
        run.rec.update({"status": "completed", "ended_at": utc(), "best": best,
                        "duration_seconds": round((datetime.now(timezone.utc) - datetime.strptime(run.rec["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds())})
        run.event("completed", best=best)
        return run.rec
    except Exception as e:  # noqa: BLE001
        run.rec.update({"status": "failed", "ended_at": utc(), "error": {"type": type(e).__name__, "message": str(e)[:2000]}})
        run.event("failed", error=type(e).__name__, message=str(e)[:500], trace=traceback.format_exc()[-2000:])
        raise
    finally:
        if locals().get("pool") is not None:
            locals()["pool"].shutdown(wait=False, cancel_futures=True)
        for s, h in old.items():
            signal.signal(s, h)


def _state(model, opt, ema, epoch, best, bad, it):
    return {"model": model.state_dict(), "optimizer": opt.state_dict(), "ema": ema.ema.state_dict() if ema else None, "ema_updates": ema.updates if ema else 0,
            "epoch": epoch, "best": best, "bad_epochs": bad, "iteration": it, "py_rng": list(random.getstate()), "torch_rng": torch.get_rng_state()}
