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
import math
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..store.db import Store
from .bpe import Tokenizer


@dataclass
class CorpusItem:
    chunk_id: int
    text: str
    lang: str = ""
    kind: str = ""
    address: str = ""
    #: The document this chunk came from. The split is made on this, never on
    #: chunk_id: chunks of one document are inserted consecutively, so hashing
    #: chunk_id puts two halves of the same document on opposite sides of the
    #: boundary and the validation set leaks into training.
    doc_id: int = 0

    @property
    def split_key(self) -> int:
        """Document identity when we have it, chunk identity when we do not."""
        return self.doc_id or self.chunk_id


def corpus_from_store(store: Store, min_chars: int = 80,
                      address_prefix: Optional[str] = None) -> List[CorpusItem]:
    """Every active chunk as training text, optionally limited to an address region.

    The region is matched on the integer address columns, not on the address
    string. A string prefix test makes "2.1" match "2.10" and "2.11", which
    are siblings of "2.1", not descendants of it.
    """
    from ..store.address import Address

    query = ("SELECT chunk_id, doc_id, text, lang, kind, address FROM chunks "
             "WHERE active = 1 AND chars >= ?")
    params: List = [min_chars]
    if address_prefix:
        region = Address.parse(address_prefix)
        columns = ["addr_domain", "addr_family", "addr_kind", "addr_cluster",
                   "addr_ordinal"][:max(1, region.depth)]
        query += " AND " + " AND ".join(f"{c} = ?" for c in columns)
        params.extend(region.segments[:len(columns)])
    query += " ORDER BY chunk_id"
    return [
        CorpusItem(int(r["chunk_id"]), r["text"] or "", r["lang"] or "",
                   r["kind"] or "", r["address"] or "", int(r["doc_id"] or 0))
        for r in store.conn.execute(query, params).fetchall()
    ]


class DatasetConfigError(ValueError):
    """A dataset build that cannot be run as asked, named clearly.

    Raised before any file is written, so a bad configuration leaves no
    half-built shard directory behind.
    """


DTYPES = {"uint16": 65_536, "uint32": 4_294_967_296}


def validate_build_args(shard_tokens: int, val_fraction: float, dtype: str,
                        vocab_size: int) -> None:
    """Everything that must be true before a single byte is written."""
    if isinstance(shard_tokens, bool) or not isinstance(shard_tokens, int):
        raise DatasetConfigError(f"shard_tokens must be an integer, got {shard_tokens!r}")
    if shard_tokens <= 0:
        raise DatasetConfigError(
            f"shard_tokens must be positive, got {shard_tokens}; a shard of zero "
            f"tokens would either write nothing or loop forever")
    if not isinstance(val_fraction, (int, float)) or isinstance(val_fraction, bool):
        raise DatasetConfigError(f"val_fraction must be a number, got {val_fraction!r}")
    if not math.isfinite(float(val_fraction)):
        raise DatasetConfigError(f"val_fraction must be finite, got {val_fraction!r}")
    if not 0.0 <= float(val_fraction) < 1.0:
        raise DatasetConfigError(
            f"val_fraction must be in [0, 1), got {val_fraction}; 1.0 would leave "
            f"no training data at all")
    if dtype not in DTYPES:
        raise DatasetConfigError(
            f"dtype must be one of {sorted(DTYPES)}, got {dtype!r}")
    if isinstance(vocab_size, bool) or not isinstance(vocab_size, int) or vocab_size <= 0:
        raise DatasetConfigError(f"vocab_size must be a positive integer, got {vocab_size!r}")
    limit = DTYPES[dtype]
    if vocab_size > limit:
        raise DatasetConfigError(
            f"vocabulary of {vocab_size:,} does not fit in {dtype} (max id {limit - 1:,}); "
            f"use dtype='uint32'")


def _is_validation(split_key: int, fraction: float) -> bool:
    """Stable by key: the same key is always on the same side.

    `split_key` is a document id in normal use, so every chunk of a document
    lands in the same split and the validation set cannot leak into training.
    """
    if fraction <= 0:
        return False
    digest = hashlib.blake2b(str(split_key).encode(), digest_size=4).digest()
    bucket = int.from_bytes(digest, "big") % 10_000
    return bucket < int(fraction * 10_000)


def build_dataset(
    items: Sequence[CorpusItem],
    tokenizer: Tokenizer,
    out_dir: str,
    *,
    shard_tokens: int = 2_000_000,
    val_fraction: float = 0.02,
    dtype: Optional[str] = None,
    tokenizer_path: str = "",
    verbose: bool = False,
) -> dict:
    """Tokenise, split, shard. Returns the manifest, also written to meta.json.

    Every argument is validated before the output directory is created, so an
    impossible configuration raises `DatasetConfigError` and leaves the disk
    untouched.

    `dtype=None` (or "auto") picks uint16 when the vocabulary fits and uint32
    when it does not, and the manifest records what was chosen. An explicit
    dtype is honoured strictly: asking for uint16 with a 70,000-token
    vocabulary is a contradiction, not something to quietly override.
    """
    if dtype is None or str(dtype).lower() == "auto":
        dtype = "uint32" if tokenizer.vocab_size >= DTYPES["uint16"] else "uint16"
    validate_build_args(shard_tokens, val_fraction, dtype, tokenizer.vocab_size)
    np_dtype = np.uint16 if dtype == "uint16" else np.uint32

    streams: dict[str, List[int]] = {"train": [], "val": []}
    counts = {"train": 0, "val": 0}
    documents: dict[str, set] = {"train": set(), "val": set()}
    for item in items:
        split = "val" if _is_validation(item.split_key, val_fraction) else "train"
        ids = tokenizer.encode(item.text, add_specials=True)
        streams[split].extend(ids)
        counts[split] += 1
        documents[split].add(item.split_key)

    # A document in both splits is a leak, and a leak makes the validation
    # loss meaningless. It cannot happen with document-keyed hashing; the
    # number is recorded so a future change to the split has to face it.
    leaked = documents["train"] & documents["val"]

    limit = DTYPES[dtype]
    for split, ids in streams.items():
        if ids and max(ids) >= limit:
            raise DatasetConfigError(
                f"token id {max(ids)} does not fit in {dtype} (max {limit - 1}); "
                f"the tokenizer and the dtype disagree")

    os.makedirs(out_dir, exist_ok=True)
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
        "document_ids": {"train": len(documents["train"]), "val": len(documents["val"])},
        "leaked_documents": len(leaked),
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
