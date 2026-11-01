"""Short training-stability probe (diagnostic only — produces no model and no record of a trained run).

    python scripts/ml/probe_training_stability.py CONFIG --lr 0.0025 --iters 100 --clip 10

Fine-tunes the configured pretrained model for a few iterations on training batches and, every 20
iterations, prints mean losses and AP50 on a fixed random sample of 60 VALIDATION images. Used in
Phase 7 to diagnose the objectness collapse of VT-EXP-001 (see docs/EXPERIMENT_LOG.md). Never reads
the test split.
"""
import argparse
import random
import sys
import time

import numpy as np
import torch

import _common  # noqa: F401
from visentra_ml.detection import config as C
from visentra_ml.detection import data as D
from visentra_ml.detection import engine as E
from visentra_ml.detection import model as Mo


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config"); ap.add_argument("--lr", type=float, required=True); ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--batch", type=int, default=16); ap.add_argument("--clip", type=float, default=0.0); ap.add_argument("--no-augment", action="store_true")
    ap.add_argument("--frozen-bn", action="store_true")
    a = ap.parse_args(argv)
    torch.set_num_threads(2); torch.manual_seed(0)
    cfg = C.load(a.config); mp = D.load_mapping(cfg)
    items, _ = D.load_items(cfg, "train", mp); vitems, _ = D.load_items(cfg, "valid", mp, subset=cfg["dataset"]["valid_subset"])
    random.seed(1); vs = random.sample(vitems, min(60, len(vitems)))
    ds = D.DetectionDataset(items, tuple(cfg["model"]["input_size"]), augment=None if a.no_augment else cfg["augment"], seed=1)
    order = D.epoch_indices(items, cfg["dataset"]["train_sampling"], random.Random(2))
    m = Mo.build(cfg["model"]["variant"], len(mp["targets"])); Mo.load_pretrained(m, cfg["model"]["pretrained"])
    opt = torch.optim.SGD(E.param_groups(m, 5e-4), lr=a.lr, momentum=0.9, nesterov=True)
    losses, t0 = [], time.time()
    for it in range(a.iters):
        m.train()
        if a.frozen_bn:
            for mod in m.modules():
                if isinstance(mod, torch.nn.BatchNorm2d):
                    mod.eval()
        imgs, t, _ = D.collate([ds[i] for i in order[it * a.batch:(it + 1) * a.batch]])
        for g in opt.param_groups:
            g["lr"] = a.lr * min(1, (it + 1) / 20) ** 2
        out = m(imgs, t); opt.zero_grad(); out["total_loss"].backward()
        if a.clip:
            torch.nn.utils.clip_grad_norm_(m.parameters(), a.clip)
        opt.step()
        losses.append([float(out[k]) for k in ("total_loss", "iou_loss", "conf_loss", "cls_loss")])
        if not np.isfinite(losses[-1][0]):
            print(f"{it + 1}: non-finite loss — diverged"); return 1
        if (it + 1) % 20 == 0:
            g, p, s = E.predict_items(m, vs, cfg["model"]["input_size"], cfg["eval"], num_classes=len(mp["targets"]))
            q = E.evaluate_preds(g, p, s, mp["targets"], match_iou=0.5, score_threshold=0.25)
            print(it + 1, "mean losses (total, iou, conf, cls)", np.array(losses[-20:]).mean(0).round(3).tolist(), "valid-sample AP50", round(q["AP50"] or 0.0, 4), f"{int(time.time() - t0)} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
