"""The embedder factory: model2vec when it is there, hash when it is not.

The model itself is not downloaded here — the hub is not reachable in CI, and a
test that needs a network is a test that fails for the wrong reason. What is
tested is the wiring: dimension discovery, normalisation, and the fallback that
keeps `vrusha` usable on a machine with no model at all.
"""
import sys
import types

import numpy as np
import pytest

ROOT = __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.config import DEFAULTS
from vrusha.store.embed import HashEmbedder, Model2VecEmbedder, get_embedder


def _fake_model2vec(dim=384, vectors=None):
    """A `model2vec` module whose StaticModel returns what the test says."""
    module = types.ModuleType("model2vec")

    class StaticModel:
        # a class body cannot read the enclosing function's `dim` for the same
        # name it assigns, so the attribute is set after the class exists
        @classmethod
        def from_pretrained(cls, name):
            instance = cls()
            instance.name = name
            return instance

        def encode(self, texts):
            if vectors is not None:
                return np.asarray(vectors, dtype=np.float32)
            base = np.arange(1, dim + 1, dtype=np.float32)
            return np.stack([base * (index + 1) for index, _ in enumerate(texts)])

    StaticModel.dim = dim
    module.StaticModel = StaticModel
    return module


def _cfg(**store):
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["store"] = {**DEFAULTS["store"], **store}
    return cfg


def test_model2vec_is_used_when_the_module_is_present(monkeypatch):
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=384))
    embedder = get_embedder(_cfg(embed_backend="model2vec"))
    assert isinstance(embedder, Model2VecEmbedder)
    assert embedder.dim == 384


def test_the_dimension_is_read_from_the_model_not_the_config(monkeypatch):
    """A 384-dim model with a 256-dim config must store 384-dim vectors."""
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=384))
    embedder = get_embedder(_cfg(embed_backend="model2vec", embed_dim=256))
    assert embedder.dim == 384
    assert embedder.encode(["hello"]).shape == (1, 384)


def test_vectors_are_unit_length(monkeypatch):
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=8))
    vectors = get_embedder(_cfg(embed_backend="model2vec")).encode(["a", "b"])
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_an_empty_input_is_an_empty_matrix(monkeypatch):
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=8))
    assert get_embedder(_cfg(embed_backend="model2vec")).encode([]).shape == (0, 8)


def test_a_missing_model_falls_back_to_hash_and_says_so(monkeypatch, capsys):
    """`import vrusha` must work on a machine with no model and no network."""
    monkeypatch.setitem(sys.modules, "model2vec", None)
    embedder = get_embedder(_cfg(embed_backend="model2vec"), verbose=True)
    assert isinstance(embedder, HashEmbedder)
    assert "model2vec unavailable" in capsys.readouterr().out


def test_the_aliases_resolve_to_the_same_backend(monkeypatch):
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=16))
    for alias in ("model2vec", "static", "potion"):
        assert isinstance(get_embedder(_cfg(embed_backend=alias)),
                          Model2VecEmbedder)


def test_the_default_backend_degrades_to_hash_without_the_package(monkeypatch):
    """The default is model2vec, but a machine with no package still works.

    This test used to assert that the default *was* hash. Wave 25 changed the
    default on the evidence in docs/BENCHMARKS.md -- `bm25 + hash` scored 8%
    recall@1, worse than bm25 alone -- so what is asserted now is the fallback,
    which is the property that must survive the change.
    """
    monkeypatch.setitem(sys.modules, "model2vec", None)
    assert isinstance(get_embedder(_cfg()), HashEmbedder)


def test_the_model_name_comes_from_config(monkeypatch):
    monkeypatch.setitem(sys.modules, "model2vec", _fake_model2vec(dim=16))
    embedder = get_embedder(_cfg(embed_backend="model2vec",
                                 embed_model_static="minishlab/potion-base-32M"))
    assert embedder.model_name == "minishlab/potion-base-32M"


# ---------------------------------------------------------------------------
# the store refuses to mix two embedders, even at the same width
# ---------------------------------------------------------------------------


def _store(tmp_path):
    import os
    from vrusha.config import ensure_dirs, resolve
    from vrusha.store.db import Store
    cfg = _cfg()
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = str(tmp_path)
    ensure_dirs(cfg)
    return Store(resolve(cfg, "db"))


def test_the_default_backend_is_model2vec():
    assert DEFAULTS["store"]["embed_backend"] == "model2vec"


def test_a_store_records_which_embedder_made_its_vectors(tmp_path):
    store = _store(tmp_path)
    assert store.embedder_name() is None
    store.set_embedder("model2vec")
    assert store.embedder_name() == "model2vec"
    store.set_embedder("model2vec")          # the same one twice is fine
    store.close()


def test_switching_embedder_at_the_same_width_is_refused(tmp_path):
    """hash and potion-base-8M are both 256 dims: width cannot catch this."""
    from vrusha.store.db import DimensionError
    store = _store(tmp_path)
    store.set_embedder("model2vec")
    with pytest.raises(DimensionError) as error:
        store.set_embedder("hash")
    message = str(error.value)
    assert "different spaces" in message
    assert "clear_vectors" in message and "ingest" in message
    store.close()


def test_a_query_with_the_wrong_embedder_warns_in_the_notes(tmp_path):
    from vrusha.store.query import retrieve
    store = _store(tmp_path)
    store.set_embedder("model2vec")
    doc = store.add_document(url="https://a.test/1", title="a",
                             text="addresses and chunks " * 40)
    chunk = store.add_chunk(doc, "an address is a path of five numbers " * 10,
                            kind="prose")
    store.set_vector(chunk, HashEmbedder(256).encode_one("address"))
    store.commit()

    result = retrieve(store, HashEmbedder(256), "what is an address", _cfg())
    assert any("different spaces" in note for note in result.notes)
    store.close()
