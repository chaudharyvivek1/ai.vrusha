"""The tokenizer: train it on your own corpus, save it, load it, use it.

Two backends, one interface.

    hf     the `tokenizers` library. Byte-level BPE, fast, the one to use.
    pure   a small byte-level BPE in plain Python. Correct, slow, and the
           reason this module works on a machine with nothing installed.

Both pin the special tokens at ids 0, 1, 2 and put the 256 byte tokens at
3..258, so a vocabulary resize can never shift them. Both are byte-level, so
no input is ever unrepresentable and there is no <unk> to pretend about.

Fertility — tokens per word — is the number to watch. Train on the text the
model will actually see; a tokenizer trained on Wikipedia and used on code
turns every identifier into four fragments and the model pays for it in
context length.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SPECIALS = ["<pad>", "<bos>", "<eos>"]
PAD_ID, BOS_ID, EOS_ID = 0, 1, 2
BYTE_OFFSET = len(SPECIALS)          # byte b is token id 3 + b
FIRST_MERGED = BYTE_OFFSET + 256     # merges start at 259

_WORD = re.compile(r"\S+")
_SPLIT = re.compile(r"\s+|\S+")     # keeps the whitespace, so decode is lossless


class TokenizerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# the wrapper
# --------------------------------------------------------------------------

class Tokenizer:
    """A trained tokenizer, whichever backend produced it."""

    def __init__(self, inner, backend: str, vocab_size: int, path: str = ""):
        self._inner = inner
        self.backend = backend
        self.vocab_size = vocab_size
        self.path = path

    # -- encoding ------------------------------------------------------
    def encode(self, text: str, add_specials: bool = False) -> List[int]:
        if not text:
            return [BOS_ID, EOS_ID] if add_specials else []
        if self.backend == "hf":
            ids = list(self._inner.encode(text).ids)
        else:
            ids = self._inner.encode(text)
        if add_specials:
            return [BOS_ID] + ids + [EOS_ID]
        return ids

    def encode_batch(self, texts: Sequence[str]) -> List[List[int]]:
        if self.backend == "hf":
            return [list(enc.ids) for enc in self._inner.encode_batch(list(texts))]
        return [self.encode(text) for text in texts]

    def decode(self, ids: Sequence[int]) -> str:
        clean = [int(i) for i in ids if int(i) >= len(SPECIALS)]
        return self._inner.decode(clean)

    def token_of(self, token_id: int) -> str:
        if self.backend == "hf":
            return self._inner.id_to_token(int(token_id)) or ""
        return self._inner.id_to_token(int(token_id))

    def fertility(self, text: str) -> float:
        """Tokens per word. The one number that says whether the vocab fits."""
        words = len(text.split())
        if not words:
            return 0.0
        return len(self.encode(text)) / words

    # -- persistence ---------------------------------------------------
    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._inner.save(path)
        with open(path + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"backend": self.backend, "vocab_size": self.vocab_size}, fh, indent=2)
        self.path = path
        return path

    @classmethod
    def load(cls, path: str) -> "Tokenizer":
        meta_path = path + ".meta.json"
        backend = "hf"
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                backend = json.load(fh).get("backend", "hf")
        if backend == "hf":
            from tokenizers import Tokenizer as _HF
            inner = _HF.from_file(path)
            return cls(inner, "hf", inner.get_vocab_size(), path)
        inner = PureBPE.load(path)
        return cls(inner, "pure", inner.vocab_size, path)


# --------------------------------------------------------------------------
# backend 1: tokenizers (byte-level BPE)
# --------------------------------------------------------------------------

def _iter_texts(source: Iterable[str]) -> Iterable[str]:
    """Accept raw strings or paths to text files."""
    for item in source:
        if os.path.isfile(item):
            with open(item, "r", encoding="utf-8", errors="ignore") as fh:
                yield fh.read()
        else:
            yield item


def _train_hf(texts: Iterable[str], vocab_size: int, min_frequency: int, path: str) -> Tokenizer:
    from tokenizers import Tokenizer as _HF
    from tokenizers import decoders, models, pre_tokenizers, trainers

    inner = _HF(models.BPE(unk_token=None))
    inner.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    inner.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size,
        min_frequency=min_frequency,
        special_tokens=SPECIALS,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        show_progress=False,
    )
    inner.train_from_iterator(_iter_texts(texts), trainer)
    tokenizer = Tokenizer(inner, "hf", inner.get_vocab_size(), path)
    if path:
        tokenizer.save(path)
    return tokenizer


# --------------------------------------------------------------------------
# backend 2: pure-python byte-level BPE
# --------------------------------------------------------------------------

def _apply_merge(symbols: Tuple[int, ...], pair: Tuple[int, int], new_id: int) -> Tuple[int, ...]:
    out: List[int] = []
    index = 0
    length = len(symbols)
    while index < length:
        if index < length - 1 and symbols[index] == pair[0] and symbols[index + 1] == pair[1]:
            out.append(new_id)
            index += 2
        else:
            out.append(symbols[index])
            index += 1
    return tuple(out)


class PureBPE:
    """Byte-level BPE over word frequencies.

    Each unique word is a tuple of token ids with a count. A merge is applied
    to the unique words only, weighted by count, which is what makes this
    finish at all. It is still O(vocab x corpus symbols): use the hf backend
    above roughly a million characters.
    """

    def __init__(self, merges: List[List[int]], vocab: List[str]):
        self.merges: List[Tuple[int, int]] = [(int(a), int(b)) for a, b in merges]
        self.vocab: List[str] = list(vocab)
        self.vocab_size = len(self.vocab)
        # pair -> (rank, resulting id)
        self._rules: Dict[Tuple[int, int], Tuple[int, int]] = {
            pair: (rank, FIRST_MERGED + rank) for rank, pair in enumerate(self.merges)
        }
        self._cache: Dict[str, List[int]] = {}

    # -- training ------------------------------------------------------
    @classmethod
    def train(cls, texts: Iterable[str], vocab_size: int, min_frequency: int = 2,
              max_chars: int = 1_000_000) -> "PureBPE":
        counts: Counter = Counter()
        seen = 0
        for text in _iter_texts(texts):
            if seen >= max_chars:
                break
            chunk = text[: max_chars - seen]
            seen += len(chunk)
            counts.update(_WORD.findall(chunk))
        if not counts:
            raise TokenizerError("no words to train on")

        words: Dict[Tuple[int, ...], int] = {}
        for word, count in counts.items():
            key = tuple(BYTE_OFFSET + b for b in word.encode("utf-8"))
            words[key] = words.get(key, 0) + count

        vocab: List[str] = list(SPECIALS)
        vocab.extend(bytes([i]).decode("latin-1") for i in range(256))
        merges: List[Tuple[int, int]] = []
        target = max(0, vocab_size - len(vocab))

        for _ in range(target):
            pairs: Counter = Counter()
            for symbols, count in words.items():
                for pair in zip(symbols, symbols[1:]):
                    pairs[pair] += count
            if not pairs:
                break
            best, best_count = max(pairs.items(), key=lambda kv: (kv[1], kv[0]))
            if best_count < min_frequency:
                break
            new_id = len(vocab)
            merges.append(best)
            vocab.append(vocab[best[0]] + vocab[best[1]])
            merged: Dict[Tuple[int, ...], int] = {}
            for symbols, count in words.items():
                key = _apply_merge(symbols, best, new_id)
                merged[key] = merged.get(key, 0) + count
            words = merged

        return cls([list(m) for m in merges], vocab)

    # -- encoding ------------------------------------------------------
    def _encode_word(self, word: str) -> List[int]:
        symbols = tuple(BYTE_OFFSET + b for b in word.encode("utf-8"))
        while len(symbols) > 1:
            best_rank: Optional[int] = None
            best_pair: Optional[Tuple[int, int]] = None
            best_id = 0
            for pair in zip(symbols, symbols[1:]):
                rule = self._rules.get(pair)
                if rule is None:
                    continue
                rank, new_id = rule
                if best_rank is None or rank < best_rank:
                    best_rank, best_pair, best_id = rank, pair, new_id
            if best_pair is None:
                break
            symbols = _apply_merge(symbols, best_pair, best_id)
        return list(symbols)

    def encode(self, text: str) -> List[int]:
        """Encode every byte. Whitespace is emitted as raw byte tokens.

        Splitting on `\\S+` and dropping the gaps would make decode lossy:
        "numeric address" would come back as "numericaddress". Merges are
        learned over words only, so a space run costs one token per space.
        """
        out: List[int] = []
        for piece in _SPLIT.findall(text):
            if not piece:
                continue
            if piece.isspace():
                out.extend(BYTE_OFFSET + b for b in piece.encode("utf-8"))
                continue
            cached = self._cache.get(piece)
            if cached is None:
                cached = self._encode_word(piece)
                if len(self._cache) < 200_000:
                    self._cache[piece] = cached
            out.extend(cached)
        return out

    def decode(self, ids: Sequence[int]) -> str:
        raw = b"".join(
            self.vocab[i].encode("latin-1") for i in ids
            if 0 <= i < len(self.vocab)
        )
        return raw.decode("utf-8", errors="replace")

    def id_to_token(self, token_id: int) -> str:
        return self.vocab[token_id] if 0 <= token_id < len(self.vocab) else ""

    # -- persistence ---------------------------------------------------
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"type": "pure-bpe", "merges": [list(m) for m in self.merges],
                       "vocab": self.vocab}, fh, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "PureBPE":
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("type") != "pure-bpe":
            raise TokenizerError(f"{path} is not a pure-bpe vocabulary")
        return cls(doc["merges"], doc["vocab"])


# --------------------------------------------------------------------------
# training entry point
# --------------------------------------------------------------------------

def train_tokenizer(texts: Iterable[str], vocab_size: int = 8192, min_frequency: int = 2,
                    out_path: str = "", backend: str = "auto") -> Tokenizer:
    """Train on `texts` (strings or file paths). `auto` prefers the hf backend."""
    texts = list(texts)
    if not texts:
        raise TokenizerError("no training text")
    chosen = backend
    if chosen == "auto":
        try:
            import tokenizers  # noqa: F401
            chosen = "hf"
        except ImportError:
            chosen = "pure"
    if chosen == "hf":
        try:
            return _train_hf(texts, vocab_size, min_frequency, out_path)
        except ImportError as exc:
            raise TokenizerError(
                "the hf backend needs `pip install tokenizers`; "
                "use backend='pure' to train without it"
            ) from exc
    inner = PureBPE.train(texts, vocab_size, min_frequency)
    tokenizer = Tokenizer(inner, "pure", inner.vocab_size, out_path)
    if out_path:
        tokenizer.save(out_path)
    return tokenizer
