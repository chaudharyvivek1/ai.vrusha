"""Wave 4 dataset-configuration tests (E3) and config-file error handling."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import ConfigError, load_config
from vrusha.tokens.dataset import (
    DatasetConfigError,
    build_dataset,
    validate_build_args,
)
from vrusha.tokens.bpe import BOS_ID, EOS_ID, train_tokenizer

TEXT = "alpha beta gamma delta epsilon zeta eta theta " * 40


class StubTokenizer:
    """Enough of a tokenizer to exercise the builder's validation."""

    def __init__(self, vocab_size=320, ids=(10, 11, 12, 13)):
        self.vocab_size = vocab_size
        self.backend = "stub"
        self._ids = list(ids)

    def encode(self, text, add_specials=False):
        base = list(self._ids)
        return ([BOS_ID] + base + [EOS_ID]) if add_specials else base


def items(n=6):
    from vrusha.tokens.dataset import CorpusItem
    return [CorpusItem(i, TEXT, "en", "paragraph", "1.1.3.1.1", doc_id=i) for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# argument validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shard_tokens", [0, -1, -1000])
def test_e3_shard_tokens_must_be_positive(shard_tokens):
    with pytest.raises(DatasetConfigError, match="shard_tokens"):
        validate_build_args(shard_tokens, 0.02, "uint16", 320)


@pytest.mark.parametrize("shard_tokens", [1.5, "1000", None, True])
def test_e3_shard_tokens_must_be_an_integer(shard_tokens):
    with pytest.raises(DatasetConfigError, match="shard_tokens"):
        validate_build_args(shard_tokens, 0.02, "uint16", 320)


@pytest.mark.parametrize("fraction", [-0.1, -1.0, 1.0, 1.5, float("nan"), float("inf")])
def test_e3_val_fraction_must_be_in_range(fraction):
    with pytest.raises(DatasetConfigError, match="val_fraction"):
        validate_build_args(1000, fraction, "uint16", 320)


def test_e3_val_fraction_zero_is_allowed():
    validate_build_args(1000, 0.0, "uint16", 320)


@pytest.mark.parametrize("dtype", ["int8", "uint8", "int16", "uint64", "float32", "", None])
def test_e3_dtype_must_be_supported(dtype):
    with pytest.raises(DatasetConfigError, match="dtype"):
        validate_build_args(1000, 0.02, dtype, 320)


def test_e3_vocab_must_fit_the_dtype():
    validate_build_args(1000, 0.02, "uint16", 65_536)          # exactly fits
    with pytest.raises(DatasetConfigError, match="uint32"):
        validate_build_args(1000, 0.02, "uint16", 65_537)


def test_e3_vocab_must_be_positive():
    with pytest.raises(DatasetConfigError, match="vocab_size"):
        validate_build_args(1000, 0.02, "uint16", 0)


# ---------------------------------------------------------------------------
# build_dataset refuses before writing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"shard_tokens": 0},
    {"shard_tokens": -5},
    {"val_fraction": 1.0},
    {"val_fraction": -0.5},
    {"dtype": "int8"},
])
def test_e3_invalid_config_leaves_no_files(tmp_path, kwargs):
    out = tmp_path / "ds"
    tokenizer = StubTokenizer()
    with pytest.raises(DatasetConfigError):
        build_dataset(items(), tokenizer, str(out), **kwargs)
    assert not out.exists(), "a refused build must not create the output directory"


def test_e3_vocab_too_big_for_uint16_is_refused(tmp_path):
    out = tmp_path / "ds"
    with pytest.raises(DatasetConfigError):
        build_dataset(items(), StubTokenizer(vocab_size=70_000), str(out),
                      dtype="uint16")
    assert not out.exists()


def test_e3_token_id_overflow_is_caught(tmp_path):
    out = tmp_path / "ds"
    # vocab says 320 but the ids are far beyond it: the tokenizer and the
    # dtype disagree, and writing this would silently truncate every id
    with pytest.raises(DatasetConfigError, match="does not fit"):
        build_dataset(items(), StubTokenizer(vocab_size=320, ids=(70_000,)), str(out),
                      dtype="uint16")
    assert not out.exists()


# ---------------------------------------------------------------------------
# valid builds still work
# ---------------------------------------------------------------------------

def test_e3_valid_build_still_writes_shards(tmp_path):
    out = tmp_path / "ds"
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    manifest = build_dataset(items(8), tokenizer, str(out), shard_tokens=500,
                             val_fraction=0.2)
    assert manifest["total_tokens"] > 0
    assert manifest["dtype"] == "uint16"
    for shard in manifest["shards"]:
        path = out / shard["name"]
        assert path.exists()
        assert path.stat().st_size == shard["tokens"] * 2
    assert (out / "meta.json").exists()


def test_e3_auto_dtype_picks_uint32_for_a_large_vocabulary(tmp_path):
    out = tmp_path / "ds"
    manifest = build_dataset(items(4), StubTokenizer(vocab_size=70_000), str(out))
    assert manifest["dtype"] == "uint32"
    for shard in manifest["shards"]:
        assert (out / shard["name"]).stat().st_size == shard["tokens"] * 4


def test_e3_auto_dtype_picks_uint16_for_a_small_vocabulary(tmp_path):
    out = tmp_path / "ds"
    manifest = build_dataset(items(4), StubTokenizer(vocab_size=320), str(out))
    assert manifest["dtype"] == "uint16"


def test_e3_large_shard_tokens_is_allowed(tmp_path):
    out = tmp_path / "ds"
    manifest = build_dataset(items(4), StubTokenizer(), str(out),
                             shard_tokens=10_000_000)
    assert len(manifest["shards"]) == 1


# ---------------------------------------------------------------------------
# config files
# ---------------------------------------------------------------------------

def test_config_malformed_json_is_a_typed_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(str(path))


def test_config_missing_file_is_a_typed_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "absent.json"))


def test_config_non_object_is_a_typed_error(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(str(path))


def test_config_valid_file_merges(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps({"query": {"top_k": 3}}), encoding="utf-8")
    cfg = load_config(str(path))
    assert cfg["query"]["top_k"] == 3
    assert cfg["query"]["candidates"] == 60        # untouched default survives
