"""Every tunable in one place, with the defaults that the tests run against.

The config is a plain dict so it round-trips through JSON without a schema
migration. `load_config` merges, in order: the defaults below, then
`config/vrusha.json` if it exists, then the file you name, then environment
variables of the form ``VRUSHA_<SECTION>_<KEY>``.

Nothing in this file reads the network or touches the store.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    "paths": {
        # Everything the pipeline writes lives under here. Override with
        # `--root` on any command, or by setting VRUSHA_PATHS_ROOT.
        "root": "data",
        "raw": "raw",
        "manifests": "manifests",
        "db": "vrusha.db",
        "vectors": "vectors",
        "tokens": "tokens",
        "models": "models",
        "logs": "logs",
        #: The ANN index. A rebuildable cache, never a source of truth.
        "index": "index",
    },
    "http": {
        "timeout": 20.0,
        "max_bytes": 3_000_000,
        #: Ceiling on the compressed body, and a separate ceiling on what it is
        #: allowed to expand to. Both are enforced while reading/decompressing.
        "max_compressed_bytes": 3_000_000,
        "max_decompressed_bytes": 20_000_000,
        "user_agent": "vrusha/0.1 (+https://github.com/chaudharyvivek1/ai.vrusha)",
        "rate_per_host": 1.0,          # seconds between requests to one host
        "respect_robots": True,
        "max_redirects": 5,
        #: Loopback and private destinations are refused. Only tests set this;
        #: nothing in the CLI does, and no rule can turn it on.
        "allow_private": False,
    },
    "harvest": {
        "max_pages": 40,               # per crawl run
        "max_depth": 2,
        "same_domain": True,
        "min_words": 120,              # below this a page is boilerplate
        "search_results": 10,
        #: simple | trafilatura | auto. The default is `simple` on purpose: a
        #: corpus must not change character because an optional package happens
        #: to be installed on one machine. Set `trafilatura` to opt in, or `auto`
        #: to use it when present. It removes boilerplate the tag stripper cannot
        #: identify -- cookie banners, sidebars, "related articles" -- on pages
        #: with enough structure for it to tell boilerplate from content. On a
        #: thin page it keeps everything, which is the safe direction.
        "extractor": "simple",
        "backend": "auto",             # auto | fixtures | ddg | searxng | tavily | brave
    },
    "store": {
        "embed_backend": "hash",       # hash | ollama | sentence-transformers
        "embed_model": "qwen3-embedding:0.6b",
        "embed_dim": 256,
        "max_chunk_lines": 60,
        "overlap_lines": 3,
        "min_chunk_chars": 80,
        "max_chunk_chars": 4000,
    },
    "address": {
        # Weights for the five segments, coarse to fine. A difference in the
        # first segment costs 16x a difference in the last, which is what
        # "far apart means different" means in practice.
        "weights": [8.0, 4.0, 2.0, 1.0, 0.5],
        "segments": 5,
    },
    "passes": {
        # Run in this order. Remove an id to skip it; the pipeline tolerates
        # a missing pass but not an unknown one.
        "order": [
            "filter.policy",
            "filter.quality",
            "filter.stale",
            "merge.exact",
            "merge.near_duplicate",
            "analyse.cluster",
            "analyse.centroid",
            "sort.stability",
            "assign.address",
            "reflow.address",
            "analyse.graph",
            "link.parent",
        ],
        "near_duplicate_threshold": 0.97,
        "stale_days": 180,
        "cluster_max": 24,
        "cluster_iters": 12,
        "graph_neighbours": 5,
        "seed": 1234,
    },
    "query": {
        "top_k": 8,
        "candidates": 60,
        # The address signal is a tiebreaker. At 0.35 it could outvote both
        # primary signals together, which let a region hint replace a correct
        # lexical hit.
        "fusion": {"bm25": 1.0, "vector": 1.0, "address": 0.15},
        "min_score": 0.0,
        # below this cosine a vector hit is noise, not a near miss
        "min_vector_similarity": 0.05,
        "refuse_below": 0.02,
    },
    "ann": {
        # auto    use the index when one exists, is fresh, and is big enough
        # force   use it even on a small corpus (for measuring it)
        # off     always scan exactly, which is the reference the index is
        #         judged against and the fallback when anything looks wrong
        "mode": "auto",
        #: Below this many vectors a full scan is faster than the routing
        #: overhead, and simpler to be sure about.
        "min_vectors": 20_000,
        "target_cell_size": 512,
        "train_sample": 100_000,
        "iters": 10,
        "seed": 13,
        #: 0 opens max(8, 10% of the cells, at most 128): the scored-row count
        #: stays roughly constant as the corpus grows, which is the point of
        #: an index. Raise it for recall, lower it for latency; the measured
        #: curve is in docs/ANN.md.
        "nprobe": 0,
        "rescore": 128,
        "ram_limit_mb": 512,
        # int8 4x smaller with a conversion per scored row; float32 needs no
        # conversion and is faster when it fits; auto picks by ram_limit_mb.
        "dtype": "auto",
    },
    "backends": {
        # auto    pick per query from what is available and measured (router)
        # exact   force the full scan, for measuring
        # off     do not use the router at all: query uses the native index
        "mode": "auto",
        #: Set a URL to bring RagForge into the rotation. Empty means the
        #: second engine is not configured, and nothing tries to reach it.
        "ragforge": {
            "url": "",
            "timeout": 2.0,
            "token_env": "RAGFORGE_TOKEN",
        },
        "cooldown_s": 30.0,      # how long a failed backend stays demoted
        "hysteresis": 0.20,      # a switch needs a 20% margin, not a tie
        "min_free_mb": 256.0,    # below this, prefer the out-of-process engine
        "shadow_every": 0,       # >0 probes the runner-up every N queries
    },
    "tokens": {
        "vocab_size": 8192,
        "min_frequency": 2,
        "backend": "auto",             # auto | hf | pure
        "shard_tokens": 2_000_000,
        "val_fraction": 0.02,
        "dtype": "uint16",
    },
    "train": {
        "preset": "tiny",
        "batch_size": 8,
        "seq_len": 256,
        "steps": 200,
        "lr": 3e-4,
        "warmup": 20,
        "weight_decay": 0.1,
        "grad_clip": 1.0,
        "eval_every": 50,
        "seed": 1234,
    },
    "rules": {
        # Level 1 is the strictest. Nothing may loosen a level-1 rule.
        "strict_level": 1,
        "warn_level": 6,
    },
}


def _env_overrides(cfg: Dict[str, Any], prefix: str = "VRUSHA_") -> None:
    """Apply VRUSHA_<SECTION>_<KEY>=value. Values are JSON if they parse."""
    for name, raw in os.environ.items():
        if not name.startswith(prefix):
            continue
        parts = name[len(prefix):].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, key = parts
        if section not in cfg or not isinstance(cfg[section], dict):
            continue
        if key not in cfg[section]:
            continue
        try:
            cfg[section][key] = json.loads(raw)
        except json.JSONDecodeError:
            cfg[section][key] = raw


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class ConfigError(ValueError):
    """A config file that cannot be read or parsed, named clearly."""


def _read_config_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg})") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a JSON object, not {type(loaded).__name__}")
    return loaded


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Defaults, then ./config/vrusha.json, then `path`, then the environment."""
    cfg = copy.deepcopy(DEFAULTS)
    local = os.path.join("config", "vrusha.json")
    if os.path.exists(local):
        _deep_merge(cfg, _read_config_file(local))
    if path:
        _deep_merge(cfg, _read_config_file(str(path)))
    _env_overrides(cfg)
    return cfg


def resolve(cfg: Dict[str, Any], key: str) -> str:
    """Absolute path for a `paths` entry. `root` resolves to itself."""
    paths = cfg["paths"]
    root = paths["root"]
    if key == "root":
        return os.path.abspath(root)
    value = paths.get(key, key)
    full = value if os.path.isabs(value) else os.path.join(root, value)
    return os.path.abspath(full)


def ensure_dirs(cfg: Dict[str, Any]) -> None:
    """Create every directory the pipeline writes to. Safe to call twice."""
    for key in ("root", "raw", "manifests", "vectors", "tokens", "models", "logs", "index"):
        os.makedirs(resolve(cfg, key), exist_ok=True)


def save_config(cfg: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
        fh.write("\n")
