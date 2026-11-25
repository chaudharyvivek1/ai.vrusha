"""Dataset: turn the store into the binary shards the trainer reads.

    chunks -> ids -> one long stream -> fixed-size shards -> train/val split

Three decisions that matter later:

  * the split is by hash of chunk_id, not by position, so re-running the build
    with a few new documents does not move an old chunk from train to val and
    silently leak it;
  * the split happens before concatenation, so no shard is half train and half
    val;
  * the dtype is uint16 when the vocabulary fits in 65,536 tokens and uint32
    otherwise, which halves the bytes on disk for every model you will train
    at this scale.

A `.bin` file is a flat array of token ids and nothing else. The sidecar
`meta.json` is the only thing that says how to read it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..store.db import Store
from .bpe import BOS_ID, EOS_ID, Tokenizer


@dataclass
class CorpusItem:
    chunk_id: int
    text: str
    lang: str = ""
    kind: str = ""
    address: str = ""


def corpus_from_store(store: Store, min_chars: int = 80,
                      address_prefix: Optional[str] = None) -> List[CorpusItem]:
    """Every active chunk as training text, optionally limited to an address region."""
    query = ("SELECT chunk_id, text, lang, kind, address FROM chunks "
             "WHERE active = 1 AND chars >= ?")
    params: List = [min_chars]
    if address_prefix:
        query += " AND address LIKE ?"
        params.append(address_prefix + "%")
    query += " ORDER BY chunk_id"
    return [
        CorpusItem(int(r["chunk_id"]), r["text"] or "", r["lang"] or "",
                   r["kind"] or "", r["address"] or "")
        for r in store.conn.execute(query, params).fetchall()
    ]


def _is_validation(chunk_id: int, fraction: float) -> bool:
    """Stable by chunk id: the same chunk is always on the same side."""
    if fraction <= 0:
        return False
    digest = hashlib.blake2b(str(chunk_id).encode(), digest_size=4).digest()
    bucket = int.from_bytes(digest, "big") % 10_000
    return bucket < int(fraction * 10_000)


def build_dataset(
    items: Sequence[CorpusItem],
    tokenizer: Tokenizer,
    out_dir: str,
    *,
    shard_tokens: int = 2_000_000,
    val_fraction: float = 0.02,
    dtype: str = "uint16",
    tokenizer_path: str = "",
    verbose: bool = False,
) -> dict:
    """Tokenise, split, shard. Returns the manifest, also written to meta.json."""
    os.makedirs(out_dir, exist_ok=True)
    if tokenizer.vocab_size >= 65_536 and dtype == "uint16":
        dtype = "uint32"
    np_dtype = np.uint16 if dtype == "uint16" else np.uint32

    streams: dict[str, List[int]] = {"train": [], "val": []}
    counts = {"train": 0, "val": 0}
    for item in items:
        split = "val" if _is_validation(item.chunk_id, val_fraction) else "train"
        ids = tokenizer.encode(item.text, add_specials=True)
        streams[split].extend(ids)
        counts[split] += 1

    shards: List[dict] = []
    total = 0
    for split, ids in streams.items():
        if not ids:
            continue
        for index in range(0, len(ids), shard_tokens):
            piece = ids[index:index + shard_tokens]
            name = f"{split}-{index // shard_tokens:05d}.bin"
            path = os.path.join(out_dir, name)
            np.asarray(piece, dtype=np_dtype).tofile(path)
            shards.append({"name": name, "split": split, "tokens": len(piece),
                           "bytes": os.path.getsize(path)})
            total += len(piece)
            if verbose:
                print(f"  wrote {name}  {len(piece):,} tokens")

    manifest = {
        "tokenizer": tokenizer_path,
        "backend": tokenizer.backend,
        "vocab_size": tokenizer.vocab_size,
        "dtype": dtype,
        "shard_tokens": shard_tokens,
        "val_fraction": val_fraction,
        "documents": counts,
        "total_tokens": total,
        "shards": shards,
        "dir": os.path.abspath(out_dir),
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def load_shard(path: str, dtype: str = "uint16") -> np.ndarray:
    np_dtype = np.uint16 if dtype == "uint16" else np.uint32
    return np.fromfile(path, dtype=np_dtype)


def read_manifest(out_dir: str) -> dict:
    with open(os.path.join(out_dir, "meta.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def batch_iterator(shards: Sequence[str], dtype: str, batch_size: int, seq_len: int,
                   seed: int = 0) -> Iterable[Tuple[np.ndarray, np.ndarray]]:
    """Infinite random batches of (x, y) where y is x shifted by one."""
    arrays = [load_shard(path, dtype) for path in shards]
    arrays = [a for a in arrays if len(a) > seq_len + 1]
    if not arrays:
        raise ValueError("no shard is longer than seq_len + 1")
    lengths = np.array([len(a) for a in arrays], dtype=np.float64)
    probabilities = lengths / lengths.sum()
    rng = np.random.default_rng(seed)
    while True:
        xs = np.empty((batch_size, seq_len), dtype=np.int64)
        ys = np.empty((batch_size, seq_len), dtype=np.int64)
        for row in range(batch_size):
            pick = int(rng.choice(len(arrays), p=probabilities))
            array = arrays[pick]
            start = int(rng.integers(0, len(array) - seq_len - 1))
            window = array[start:start + seq_len + 1].astype(np.int64)
            xs[row] = window[:-1]
            ys[row] = window[1:]
        yield xs, ys
