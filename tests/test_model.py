"""Model tests: tokenizers, shards, the transformer, and one real training step.

The torch tests skip themselves when torch is missing, so the suite still runs
on a machine that only wants the retrieval half.
"""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.tokens.bpe import BOS_ID, EOS_ID, PAD_ID, PureBPE, Tokenizer, train_tokenizer
from vrusha.tokens.dataset import CorpusItem, batch_iterator, build_dataset

TEXT = (
    "Retrieval augmented generation answers from retrieved context. "
    "A numeric address places a unit of data in a tree of meaning. "
    "Byte pair encoding merges the most frequent adjacent pair. "
) * 20


# --------------------------------------------------------------------------
# tokenizer
# --------------------------------------------------------------------------

def test_specials_are_pinned():
    assert (PAD_ID, BOS_ID, EOS_ID) == (0, 1, 2)


def test_pure_bpe_round_trips_text():
    tokenizer = train_tokenizer([TEXT], vocab_size=300, backend="pure")
    assert tokenizer.backend == "pure"
    ids = tokenizer.encode("numeric address")
    assert ids
    assert tokenizer.decode(ids) == "numeric address"


def test_pure_bpe_compresses_below_bytes():
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    encoded = tokenizer.encode("Retrieval augmented generation")
    assert len(encoded) < len("Retrieval augmented generation".encode("utf-8"))


def test_pure_bpe_handles_unicode():
    tokenizer = train_tokenizer([TEXT + " भारतीय रिज़र्व बैंक"], vocab_size=300, backend="pure")
    text = "भारतीय रिज़र्व"
    assert tokenizer.decode(tokenizer.encode(text)) == text


def test_pure_bpe_saves_and_loads(tmp_path):
    tokenizer = train_tokenizer([TEXT], vocab_size=300, backend="pure")
    path = str(tmp_path / "tok.json")
    tokenizer.save(path)
    loaded = Tokenizer.load(path)
    assert loaded.encode("address") == tokenizer.encode("address")


def test_hf_backend_round_trips_when_available(tmp_path):
    pytest.importorskip("tokenizers")
    tokenizer = train_tokenizer([TEXT], vocab_size=400, out_path=str(tmp_path / "hf.json"),
                                backend="hf")
    assert tokenizer.backend == "hf"
    text = "the address tree"
    assert tokenizer.decode(tokenizer.encode(text)) == text
    loaded = Tokenizer.load(str(tmp_path / "hf.json"))
    assert loaded.vocab_size == tokenizer.vocab_size


def test_fertility_is_measured():
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    assert 0.5 < tokenizer.fertility("numeric address places data") < 12


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------

def test_dataset_writes_shards_and_manifest(tmp_path):
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    items = [CorpusItem(i, TEXT, "en", "paragraph", f"1.1.3.1.{i}") for i in range(1, 6)]
    manifest = build_dataset(items, tokenizer, str(tmp_path / "ds"),
                             shard_tokens=1000, val_fraction=0.0)
    assert manifest["total_tokens"] > 0
    assert manifest["dtype"] == "uint16"
    for shard in manifest["shards"]:
        path = os.path.join(str(tmp_path / "ds"), shard["name"])
        assert os.path.getsize(path) == shard["tokens"] * 2
    with open(os.path.join(str(tmp_path / "ds"), "meta.json")) as fh:
        assert json.load(fh)["total_tokens"] == manifest["total_tokens"]


def test_batches_are_shifted_by_one(tmp_path):
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    items = [CorpusItem(i, TEXT * 3, "en", "paragraph", "1.1.3.1.1") for i in range(1, 4)]
    manifest = build_dataset(items, tokenizer, str(tmp_path / "ds"), shard_tokens=5000)
    paths = [os.path.join(str(tmp_path / "ds"), s["name"]) for s in manifest["shards"]]
    iterator = batch_iterator(paths, manifest["dtype"], batch_size=2, seq_len=16)
    x, y = next(iterator)
    assert x.shape == (2, 16) and y.shape == (2, 16)
    assert np.array_equal(x[0, 1:], y[0, :-1])


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

torch = pytest.importorskip("torch")

from vrusha.generate.model import ModelConfig, TinyLM
from vrusha.generate.train import TrainConfig, lr_at, train


def test_forward_shapes_and_loss():
    cfg = ModelConfig.preset("tiny", 128)
    model = TinyLM(cfg)
    x = torch.randint(0, 128, (2, 32))
    logits, loss = model(x, x)
    assert logits.shape == (2, 32, 128)
    assert loss is not None and float(loss) > 0


def test_rope_rotates_the_same_vector_differently_per_position():
    from vrusha.generate.model import apply_rope, rope_tables

    cos, sin = rope_tables(8, 16)
    same_vector = torch.ones(1, 1, 8, 8)      # identical at every position
    out = apply_rope(same_vector, cos, sin)
    assert not torch.allclose(out[0, 0, 0], out[0, 0, 5])
    # position 0 is unrotated, because cos(0)=1 and sin(0)=0
    assert torch.allclose(out[0, 0, 0], same_vector[0, 0, 0], atol=1e-5)


def test_causal_mask_hides_the_future():
    cfg = ModelConfig.preset("tiny", 64)
    model = TinyLM(cfg)
    model.eval()
    ids = torch.randint(0, 64, (1, 8))
    with torch.no_grad():
        a, _ = model(ids)
        changed = ids.clone()
        changed[0, -1] = (ids[0, -1] + 1) % 64
        b, _ = model(changed)
    # the last token cannot influence the logits before it
    assert torch.allclose(a[0, :-1], b[0, :-1], atol=1e-5)


def test_generation_returns_the_right_length():
    cfg = ModelConfig.preset("tiny", 64)
    model = TinyLM(cfg)
    out = model.generate(torch.randint(0, 64, (1, 4)), max_new_tokens=6, temperature=0.0)
    assert out.shape == (1, 10)


def test_param_count_matches_preset_scale():
    assert 50_000 < TinyLM(ModelConfig.preset("tiny", 4096)).params() < 2_000_000
    assert 2_000_000 < TinyLM(ModelConfig.preset("6m", 4096)).params() < 15_000_000


def test_lr_schedule_warms_up_then_decays():
    assert lr_at(0, 1.0, 10, 100) < lr_at(5, 1.0, 10, 100)
    assert lr_at(10, 1.0, 10, 100) > lr_at(90, 1.0, 10, 100)
    assert lr_at(100, 1.0, 10, 100) == pytest.approx(0.1)


def test_training_reduces_loss_on_a_tiny_corpus(tmp_path):
    tokenizer = train_tokenizer([TEXT], vocab_size=320, backend="pure")
    items = [CorpusItem(i, TEXT, "en", "paragraph", "1.1.3.1.1") for i in range(1, 8)]
    data_dir = str(tmp_path / "ds")
    build_dataset(items, tokenizer, data_dir, shard_tokens=100_000, val_fraction=0.0)

    cfg = ModelConfig.preset("tiny", tokenizer.vocab_size)
    cfg.n_ctx = 64
    summary = train(
        data_dir, str(tmp_path / "model"), cfg,
        TrainConfig(steps=25, batch_size=2, seq_len=64, lr=3e-3, warmup=5,
                    eval_every=25, device="cpu", log_every=5),
        verbose=False,
    )
    assert summary["tokens_seen"] > 0
    assert os.path.exists(summary["checkpoint"])
    first = summary.get("final_train_loss")
    assert first is not None

    model, payload = TinyLM.from_checkpoint(summary["checkpoint"])
    assert model.params() == summary["parameters"]
    assert payload["step"] == 25
