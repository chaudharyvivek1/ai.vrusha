"""Embeddings: three backends, one interface, one that always works.

    hash                   needs nothing. Deterministic feature hashing of words
                           and character n-grams into `dim` buckets, L2
                           normalised. It is not a language model and it does
                           not understand synonyms; it is a reproducible
                           baseline that makes the pipeline runnable and the
                           tests honest. Upgrade when you care about recall.
    ollama                 a local embedding server (`ollama pull qwen3-embedding:0.6b`)
    sentence-transformers  in-process, needs the package and a model download

`get_embedder` returns the configured backend, or the hash backend with a
printed reason. It never raises because a backend is missing: the pipeline
degrading to a working baseline beats the pipeline refusing to start.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from typing import Dict, List, Optional, Sequence

import numpy as np

_WORD = re.compile(r"[a-z0-9_]+")


class Embedder:
    name = "base"
    dim = 256

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class HashEmbedder(Embedder):
    """Deterministic feature hashing. Same text, same vector, forever."""

    name = "hash"

    def __init__(self, dim: int = 256, char_ngrams: int = 3):
        self.dim = int(dim)
        self.char_ngrams = char_ngrams

    def _features(self, text: str) -> Dict[str, float]:
        low = text.lower()
        counts: Dict[str, float] = {}
        for word in _WORD.findall(low):
            counts["w:" + word] = counts.get("w:" + word, 0.0) + 1.0
            # a short prefix catches morphology without a stemmer
            if len(word) > 4:
                counts["p:" + word[:4]] = counts.get("p:" + word[:4], 0.0) + 0.5
        compact = re.sub(r"\s+", " ", low)
        n = self.char_ngrams
        for i in range(0, max(0, len(compact) - n), max(1, n // 2)):
            gram = compact[i:i + n]
            if gram.strip():
                counts["c:" + gram] = counts.get("c:" + gram, 0.0) + 0.25
        return counts

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            features = self._features(text or "")
            if not features:
                continue
            for token, weight in features.items():
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dim
                sign = 1.0 if (value >> 63) & 1 else -1.0
                out[row, index] += sign * (1.0 + np.log1p(weight))
            norm = np.linalg.norm(out[row])
            if norm > 0:
                out[row] /= norm
        return out


class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, model: str = "qwen3-embedding:0.6b", url: str = "http://localhost:11434",
                 dim: int = 1024):
        self.model = model
        self.url = url.rstrip("/")
        self.dim = dim

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=3) as response:
                return response.status == 200
        except Exception:
            return False

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        rows = []
        for text in texts:
            payload = json.dumps({"model": self.model, "prompt": text}).encode()
            request = urllib.request.Request(
                f"{self.url}/api/embeddings", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=60) as response:
                vector = json.loads(response.read().decode())["embedding"]
            rows.append(vector)
        matrix = np.asarray(rows, dtype=np.float32)
        self.dim = matrix.shape[1]
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms


class Model2VecEmbedder(Embedder):
    """Static embeddings: a distilled table of token vectors, averaged.

    `model2vec` turns a sentence transformer into a lookup table — ~8 MB for the
    smallest model, no torch, no GPU, and up to 500x faster on a CPU than the
    model it was distilled from. That is the right trade for this project, whose
    default embedder is a hash function and whose target machine is a laptop.

    The text is uncontextualised: the vector for a word is the same in every
    sentence. That is a real loss on some queries and a real gain on cost, and
    the honest place to say so is here rather than in a benchmark table.

    Loaded lazily: `import vrusha` must not require the model, the hub, or the
    network. If it is missing, `get_embedder` falls back to hash and says why.
    """

    name = "model2vec"

    def __init__(self, model: str = "minishlab/potion-base-8M",
                 dim: Optional[int] = None):
        from model2vec import StaticModel       # optional dependency

        self.model_name = model
        self._model = StaticModel.from_pretrained(model)
        width = 0
        for attribute in ("dim", "dimensionality"):
            value = getattr(self._model, attribute, None)
            if isinstance(value, int) and value > 0:
                width = value
                break
        if not width:
            embedding = getattr(self._model, "embedding", None)
            shape = getattr(embedding, "shape", None)
            width = int(shape[1]) if shape and len(shape) > 1 else 0
        self.dim = int(width or dim or 256)

    def available(self) -> bool:
        return self._model is not None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = np.asarray(self._model.encode(list(texts)), dtype=np.float32)
        # cosine, as everywhere else in this store: a dot product of unit vectors
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.clip(norms, 1e-12, None)


class SentenceTransformerEmbedder(Embedder):
    name = "sentence-transformers"

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # noqa: F401
        self._model = SentenceTransformer(model)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = self._model.encode(list(texts), normalize_embeddings=True,
                                    convert_to_numpy=True).astype(np.float32)
        self.dim = matrix.shape[1]
        return matrix


def get_embedder(cfg: dict, verbose: bool = False) -> Embedder:
    """The configured backend, or the hash backend with the reason printed."""
    store_cfg = cfg.get("store", {})
    backend = (store_cfg.get("embed_backend") or "hash").lower()
    dim = int(store_cfg.get("embed_dim", 256))
    model = store_cfg.get("embed_model", "qwen3-embedding:0.6b")

    if backend in ("hash", "default"):
        return HashEmbedder(dim)

    if backend == "ollama":
        embedder = OllamaEmbedder(model, dim=1024)
        if embedder.available():
            return embedder
        if verbose:
            print(f"  embedder: ollama not reachable at {embedder.url}; using hash")
        return HashEmbedder(dim)

    if backend in ("model2vec", "static", "potion"):
        name = store_cfg.get("embed_model_static", "minishlab/potion-base-8M")
        try:
            return Model2VecEmbedder(name, dim)
        except Exception as exc:                # noqa: BLE001 - absent or no hub
            if verbose:
                print(f"  embedder: model2vec unavailable "
                      f"({type(exc).__name__}: {exc}); using hash")
            return HashEmbedder(dim)

    if backend in ("sentence-transformers", "st", "minilm"):
        try:
            return SentenceTransformerEmbedder()
        except Exception as exc:
            if verbose:
                print(f"  embedder: sentence-transformers unavailable ({type(exc).__name__}); using hash")
            return HashEmbedder(dim)

    if verbose:
        print(f"  embedder: unknown backend {backend!r}; using hash")
    return HashEmbedder(dim)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for two already-normalised vectors."""
    return float(np.dot(a, b))


def pack(vector: np.ndarray, dtype: str = "float32") -> tuple[bytes, str, int]:
    """Serialise a vector for the store. int8 halves nothing and costs recall."""
    array = np.asarray(vector, dtype=np.float32)
    if dtype == "int8":
        scaled = np.clip(array * 127.0, -127, 127).astype(np.int8)
        return scaled.tobytes(), "int8", int(array.shape[0])
    return array.astype(np.float32).tobytes(), "float32", int(array.shape[0])


def unpack(blob: bytes, dtype: str, dim: int) -> np.ndarray:
    if dtype == "int8":
        return (np.frombuffer(blob, dtype=np.int8).astype(np.float32) / 127.0)[:dim]
    return np.frombuffer(blob, dtype=np.float32)[:dim]
