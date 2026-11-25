"""TinyLM: a decoder-only transformer small enough to train on one GPU, or on
a CPU if you are patient and honest about what that buys you.

    embedding (tied to the output projection)
    x N blocks:  RMSNorm -> causal attention with RoPE -> residual
                 RMSNorm -> SwiGLU feed-forward        -> residual
    final RMSNorm -> logits over the vocabulary

Design choices, and why:

  * RoPE, not learned positions: the model extrapolates a little past its
    training length, and there is no position table to get out of step with a
    resized vocabulary.
  * RMSNorm, not LayerNorm: one fewer parameter and one fewer operation, no
    measurable loss at this scale.
  * SwiGLU, not ReLU: about the same parameters buy more quality.
  * tied embeddings: at a 20k vocabulary the embedding table is a large share
    of the model. Tying it saves half of that and costs almost nothing.
  * no biases: they contribute little and complicate checkpoint surgery.

Presets are measured, not guessed: `params()` counts what the model actually
has, and `vrusha doctor` prints it against your token count so the ratio is
visible before you spend a night training.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

PRESETS: Dict[str, dict] = {
    "tiny":  {"d_model": 64,  "n_layer": 2, "n_head": 2, "n_ctx": 256, "dropout": 0.0},
    "6m":    {"d_model": 192, "n_layer": 6, "n_head": 6, "n_ctx": 256, "dropout": 0.0},
    "20m":   {"d_model": 384, "n_layer": 6, "n_head": 6, "n_ctx": 256, "dropout": 0.0},
    "50m":   {"d_model": 512, "n_layer": 8, "n_head": 8, "n_ctx": 512, "dropout": 0.0},
}


@dataclass
class ModelConfig:
    vocab_size: int = 8192
    d_model: int = 64
    n_layer: int = 2
    n_head: int = 2
    n_ctx: int = 256
    dropout: float = 0.0
    tie_embeddings: bool = True

    @classmethod
    def preset(cls, name: str, vocab_size: int) -> "ModelConfig":
        if name not in PRESETS:
            raise KeyError(f"unknown preset {name!r}; have {sorted(PRESETS)}")
        return cls(vocab_size=vocab_size, **PRESETS[name])

    @property
    def head_dim(self) -> int:
        return self.d_model // self.n_head

    @property
    def hidden(self) -> int:
        """SwiGLU inner width, rounded to a multiple of 8 for the tensor cores."""
        raw = int(8 * self.d_model / 3)
        return 8 * ((raw + 7) // 8)


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        norm = x.float().pow(2).mean(-1, keepdim=True).add(self.eps).rsqrt()
        return (x.float() * norm).type_as(x) * self.weight


def rope_tables(dim: int, max_len: int, base: float = 10_000.0,
                device: Optional[torch.device] = None) -> Tuple[torch.Tensor, torch.Tensor]:
    inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32, device=device) / dim))
    positions = torch.arange(max_len, dtype=torch.float32, device=device)
    freqs = torch.outer(positions, inv_freq)                 # (T, dim/2)
    return freqs.cos(), freqs.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """x: (B, H, T, D). Rotate each adjacent pair by its position."""
    length = x.shape[-2]                     # T, not H: -3 would be the head count
    cos = cos[:length].unsqueeze(0).unsqueeze(0)
    sin = sin[:length].unsqueeze(0).unsqueeze(0)
    pairs = x.float().reshape(*x.shape[:-1], -1, 2)
    even, odd = pairs[..., 0], pairs[..., 1]
    rotated = torch.stack((even * cos - odd * sin, even * sin + odd * cos), dim=-1)
    return rotated.reshape_as(x).type_as(x)


class Attention(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.n_head = cfg.n_head
        self.head_dim = cfg.head_dim
        self.qkv = nn.Linear(cfg.d_model, 3 * cfg.d_model, bias=False)
        self.out = nn.Linear(cfg.d_model, cfg.d_model, bias=False)
        self.dropout = cfg.dropout

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        batch, length, dim = x.shape
        qkv = self.qkv(x).split(dim, dim=2)
        q, k, v = [
            t.view(batch, length, self.n_head, self.head_dim).transpose(1, 2)
            for t in qkv
        ]
        q = apply_rope(q, cos, sin)
        k = apply_rope(k, cos, sin)
        out = F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=True,
        )
        out = out.transpose(1, 2).contiguous().view(batch, length, dim)
        return self.out(out)


class SwiGLU(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.gate = nn.Linear(cfg.d_model, cfg.hidden, bias=False)
        self.up = nn.Linear(cfg.d_model, cfg.hidden, bias=False)
        self.down = nn.Linear(cfg.hidden, cfg.d_model, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down(F.silu(self.gate(x)) * self.up(x))


class Block(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.norm1 = RMSNorm(cfg.d_model)
        self.attn = Attention(cfg)
        self.norm2 = RMSNorm(cfg.d_model)
        self.ffn = SwiGLU(cfg)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x), cos, sin)
        return x + self.ffn(self.norm2(x))


class TinyLM(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg
        self.embed = nn.Embedding(cfg.vocab_size, cfg.d_model)
        self.blocks = nn.ModuleList(Block(cfg) for _ in range(cfg.n_layer))
        self.norm = RMSNorm(cfg.d_model)
        self.head = nn.Linear(cfg.d_model, cfg.vocab_size, bias=False)
        if cfg.tie_embeddings:
            self.head.weight = self.embed.weight
        cos, sin = rope_tables(cfg.head_dim, cfg.n_ctx)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)
        self.apply(self._init)

    @staticmethod
    def _init(module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    # ------------------------------------------------------------------
    def params(self, non_embedding: bool = False) -> int:
        total = sum(p.numel() for p in self.parameters())
        if non_embedding:
            total -= self.embed.weight.numel()
        return int(total)

    def forward(self, idx: torch.Tensor, targets: Optional[torch.Tensor] = None
                ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        length = idx.shape[1]
        if length > self.cfg.n_ctx:
            raise ValueError(f"sequence length {length} exceeds n_ctx {self.cfg.n_ctx}")
        x = self.embed(idx)
        for block in self.blocks:
            x = block(x, self.rope_cos, self.rope_sin)
        x = self.norm(x)
        logits = self.head(x)
        loss = None
        if targets is not None:
            loss = F.cross_entropy(
                logits.view(-1, logits.size(-1)), targets.reshape(-1), ignore_index=-1)
        return logits, loss

    # ------------------------------------------------------------------
    @torch.no_grad()
    def generate(self, idx: torch.Tensor, max_new_tokens: int = 64, temperature: float = 0.8,
                 top_k: Optional[int] = 50, eos_id: Optional[int] = None) -> torch.Tensor:
        """Greedy at temperature 0, nucleus-free top-k otherwise.

        No KV cache: it recomputes the prefix every step. At 20M parameters and
        a few hundred tokens that is imperceptible, and it is one less thing to
        get wrong.
        """
        self.eval()
        for _ in range(max_new_tokens):
            window = idx[:, -self.cfg.n_ctx:]
            logits, _ = self(window)
            logits = logits[:, -1, :]
            if temperature <= 0:
                next_id = torch.argmax(logits, dim=-1, keepdim=True)
            else:
                logits = logits / temperature
                if top_k:
                    k = min(top_k, logits.size(-1))
                    values, _ = torch.topk(logits, k)
                    logits[logits < values[:, [-1]]] = -float("inf")
                probabilities = F.softmax(logits, dim=-1)
                next_id = torch.multinomial(probabilities, num_samples=1)
            idx = torch.cat((idx, next_id), dim=1)
            if eos_id is not None and int(next_id[0, 0]) == eos_id:
                break
        return idx

    # ------------------------------------------------------------------
    def save_checkpoint(self, path: str, step: int = 0, extra: Optional[dict] = None) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        payload = {
            "model": self.state_dict(),
            "config": asdict(self.cfg),
            "step": step,
            "extra": extra or {},
        }
        torch.save(payload, path)
        return path

    @classmethod
    def from_checkpoint(cls, path: str, map_location: str = "cpu") -> Tuple["TinyLM", dict]:
        payload = torch.load(path, map_location=map_location, weights_only=False)
        cfg = ModelConfig(**payload["config"])
        model = cls(cfg)
        model.load_state_dict(payload["model"])
        model.eval()
        return model, payload


def build_model(cfg: ModelConfig) -> TinyLM:
    return TinyLM(cfg)
