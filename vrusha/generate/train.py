"""Training: the loop, the schedule, and the log that lets you judge a run.

One decision shapes everything here: the model is judged on held-out loss and
on samples you read, never on training loss alone. Training loss falls for
three different reasons — learning, memorising, and a broken mask — and only
the held-out number and your eyes tell them apart.

Every run writes `train_log.jsonl`, one JSON object per log line, so a run can
be plotted, compared and resumed without parsing prose.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn.functional as F

from ..tokens.dataset import batch_iterator, read_manifest
from .model import ModelConfig, TinyLM, build_model


def pick_device(requested: str = "auto") -> torch.device:
    if requested and requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def lr_at(step: int, base_lr: float, warmup: int, total: int, min_ratio: float = 0.1) -> float:
    """Linear warmup, then cosine decay to `min_ratio` of the peak."""
    if step < warmup:
        return base_lr * (step + 1) / max(1, warmup)
    if step >= total:
        return base_lr * min_ratio
    progress = (step - warmup) / max(1, total - warmup)
    return base_lr * (min_ratio + (1 - min_ratio) * 0.5 * (1 + math.cos(math.pi * progress)))


@dataclass
class TrainConfig:
    steps: int = 200
    batch_size: int = 8
    seq_len: int = 256
    lr: float = 3e-4
    warmup: int = 20
    weight_decay: float = 0.1
    grad_clip: float = 1.0
    eval_every: int = 50
    eval_batches: int = 4
    seed: int = 1234
    device: str = "auto"
    keep_checkpoints: int = 2
    log_every: int = 10


def _evaluate(model: TinyLM, batches: List[tuple], device: torch.device) -> float:
    model.eval()
    losses = []
    with torch.no_grad():
        for x_np, y_np in batches:
            x = torch.from_numpy(x_np).to(device)
            y = torch.from_numpy(y_np).to(device)
            _logits, loss = model(x, y)
            if loss is not None:
                losses.append(float(loss))
    model.train()
    return float(np.mean(losses)) if losses else float("nan")


def train(
    data_dir: str,
    out_dir: str,
    config: ModelConfig,
    train_cfg: TrainConfig,
    *,
    tokenizer_path: str = "",
    verbose: bool = True,
    resume: Optional[str] = None,
) -> dict:
    """Train, checkpoint, log. Returns a summary dict."""
    manifest = read_manifest(data_dir)
    dtype = manifest.get("dtype", "uint16")
    train_shards = [os.path.join(data_dir, s["name"]) for s in manifest["shards"] if s["split"] == "train"]
    val_shards = [os.path.join(data_dir, s["name"]) for s in manifest["shards"] if s["split"] == "val"]
    if not train_shards:
        raise ValueError(f"no train shards in {data_dir}")

    torch.manual_seed(train_cfg.seed)
    np.random.seed(train_cfg.seed)
    device = pick_device(train_cfg.device)

    model = build_model(config).to(device)
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=train_cfg.lr,
        betas=(0.9, 0.95), weight_decay=train_cfg.weight_decay)

    start_step = 0
    if resume and os.path.exists(resume):
        payload = torch.load(resume, map_location=device, weights_only=False)
        model.load_state_dict(payload["model"])
        if "optimizer" in payload:
            optimizer.load_state_dict(payload["optimizer"])
        start_step = int(payload.get("step", 0))

    train_iter = batch_iterator(train_shards, dtype, train_cfg.batch_size,
                                train_cfg.seq_len, seed=train_cfg.seed)
    has_val = bool(val_shards)
    val_iter = batch_iterator(val_shards or train_shards, dtype, train_cfg.batch_size,
                              train_cfg.seq_len, seed=train_cfg.seed + 1)
    val_batches = [next(val_iter) for _ in range(max(1, train_cfg.eval_batches))]
    if not has_val and verbose:
        print("  warning     no validation shard: the loss below is on training data "
              "and cannot tell you whether the model is learning or memorising")

    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "train_log.jsonl")
    history: List[dict] = []
    tokens_seen = 0
    started = time.time()

    if verbose:
        print(f"  device      {device}")
        print(f"  parameters  {model.params():,}  ({model.params(non_embedding=True):,} non-embedding)")
        print(f"  corpus      {manifest['total_tokens']:,} tokens, {len(train_shards)} train shard(s)")
        print(f"  steps       {train_cfg.steps}  batch {train_cfg.batch_size} x {train_cfg.seq_len}")

    for step in range(start_step, train_cfg.steps):
        lr = lr_at(step, train_cfg.lr, train_cfg.warmup, train_cfg.steps)
        for group in optimizer.param_groups:
            group["lr"] = lr
        x_np, y_np = next(train_iter)
        x = torch.from_numpy(x_np).to(device)
        y = torch.from_numpy(y_np).to(device)
        _logits, loss = model(x, y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        if train_cfg.grad_clip:
            torch.nn.utils.clip_grad_norm_(model.parameters(), train_cfg.grad_clip)
        optimizer.step()
        tokens_seen += x.numel()

        if (step + 1) % train_cfg.log_every == 0 or step == start_step:
            record = {
                "step": step + 1,
                "loss": round(float(loss), 4),
                "lr": round(lr, 6),
                "tokens": tokens_seen,
                "elapsed_s": round(time.time() - started, 1),
            }
            history.append(record)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            if verbose:
                print(f"  step {step + 1:>5}  loss {record['loss']:.4f}  lr {lr:.2e}  "
                      f"{tokens_seen / max(1e-9, time.time() - started):,.0f} tok/s")

        if (step + 1) % train_cfg.eval_every == 0 or step + 1 == train_cfg.steps:
            val_loss = _evaluate(model, val_batches, device)
            record = {"step": step + 1, "val_loss": round(val_loss, 4),
                      "perplexity": round(math.exp(val_loss), 3) if val_loss < 50 else None}
            history.append(record)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            if verbose:
                print(f"  eval at {step + 1:>5}  val loss {val_loss:.4f}  "
                      f"ppl {math.exp(val_loss):.2f}" if val_loss < 50
                      else f"  eval at {step + 1:>5}  val loss {val_loss:.4f}")
            checkpoint = os.path.join(out_dir, f"step_{step + 1:06d}.pt")
            model.save_checkpoint(checkpoint, step=step + 1, extra={"val_loss": val_loss})
            _rotate(out_dir, train_cfg.keep_checkpoints)

    latest = os.path.join(out_dir, "latest.pt")
    model.save_checkpoint(latest, step=train_cfg.steps,
                          extra={"tokens_seen": tokens_seen, "tokenizer": tokenizer_path})
    summary = {
        "parameters": model.params(),
        "steps": train_cfg.steps,
        "tokens_seen": tokens_seen,
        "wall_clock_s": round(time.time() - started, 1),
        "final_train_loss": next((h["loss"] for h in reversed(history) if "loss" in h), None),
        "final_val_loss": next((h["val_loss"] for h in reversed(history) if "val_loss" in h), None),
        "checkpoint": latest,
        "log": log_path,
        "device": str(device),
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def _rotate(out_dir: str, keep: int) -> None:
    """Keep the last `keep` step checkpoints. `latest.pt` is never removed."""
    if keep <= 0:
        return
    steps = sorted(
        (os.path.join(out_dir, name) for name in os.listdir(out_dir)
         if name.startswith("step_") and name.endswith(".pt")),
        key=os.path.getmtime)
    for path in steps[:-keep]:
        try:
            os.remove(path)
        except OSError:
            pass
