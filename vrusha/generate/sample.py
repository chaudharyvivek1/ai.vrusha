"""Sampling: load a checkpoint, give it a prompt, read what comes out.

Read the output with one question in mind: is this repeating a template, or is
it composing? A small model trained on too little text produces fluent
repetition. That is not a bug in the sampler and no temperature will fix it;
it is the corpus telling you it was too small.
"""
from __future__ import annotations

import os
from typing import Optional

import torch

from ..tokens.bpe import BOS_ID, EOS_ID, Tokenizer
from .model import TinyLM
from .train import pick_device


def generate_text(
    checkpoint: str,
    prompt: str,
    *,
    tokenizer_path: str = "",
    max_new_tokens: int = 120,
    temperature: float = 0.8,
    top_k: Optional[int] = 50,
    device: str = "auto",
    seed: Optional[int] = None,
) -> dict:
    if not os.path.exists(checkpoint):
        raise FileNotFoundError(f"no checkpoint at {checkpoint}")
    chosen = pick_device(device)
    model, payload = TinyLM.from_checkpoint(checkpoint, map_location=str(chosen))
    model.to(chosen)

    tokenizer_path = tokenizer_path or (payload.get("extra") or {}).get("tokenizer") or ""
    if not tokenizer_path or not os.path.exists(tokenizer_path):
        raise FileNotFoundError(
            "no tokenizer for this checkpoint; pass --tokenizer (the .pt stores "
            "the path it was trained with, which may have moved)")
    tokenizer = Tokenizer.load(tokenizer_path)

    if seed is not None:
        torch.manual_seed(seed)

    ids = tokenizer.encode(prompt, add_specials=True) if prompt else [BOS_ID]
    idx = torch.tensor([ids], dtype=torch.long, device=chosen)
    out = model.generate(idx, max_new_tokens=max_new_tokens, temperature=temperature,
                         top_k=top_k, eos_id=EOS_ID)
    completion = tokenizer.decode(out[0, len(ids):].tolist())
    return {
        "checkpoint": checkpoint,
        "prompt": prompt,
        "completion": completion.strip(),
        "full": tokenizer.decode(out[0].tolist()).strip(),
        "tokens": int(out.shape[1] - len(ids)),
        "device": str(chosen),
        "parameters": model.params(),
    }
