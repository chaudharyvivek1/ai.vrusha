"""Every work is tested, and every work is benchmarked.

A test says the behaviour is right; a benchmark says what it costs. The rule is
that a module has both — so this test walks `vrusha/` and fails when a module has
neither a benchmark nor a written reason for not having one.

That is what makes the rule enforceable rather than aspirational.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bench import registry as benchmarks


def _modules():
    found = []
    for base, dirs, names in os.walk(os.path.join(ROOT, "vrusha")):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            found.append(os.path.relpath(path, ROOT))
    return found


def test_every_module_is_benchmarked_or_exempt_with_a_reason():
    """The rule: no module ships without a benchmark or a sentence saying why."""
    measured = {
        "vrusha/store/blackboard.py": "blackboard",
        "vrusha/store/claims.py": "claims",
        "vrusha/store/registry.py": "registry",
        "vrusha/store/query.py": "address",
        "vrusha/rerank.py": "rerank",
        "vrusha/evaluate.py": "retrieval",
        "vrusha/ingest.py": "ingest",
        "vrusha/serve.py": "serve",
        "vrusha/store/extract.py": "extract",
        "vrusha/store/records.py": "extract",
        "vrusha/store/delta.py": "index_delta",
        "vrusha/store/ann.py": "index_delta",
        "vrusha/store/snapshot.py": "snapshot",
        "vrusha/store/prose.py": "entities",
        "vrusha/store/edges.py": "edges",
        "vrusha/store/communities.py": "communities",
        "vrusha/store/paths.py": "paths",
    }
    exempt = benchmarks.EXEMPT
    missing = []
    for path in _modules():
        if path in measured or path in exempt:
            continue
        missing.append(path)
    assert not missing, (
        "these modules have no benchmark and no reason:\n  " + "\n  ".join(missing))


def test_every_exemption_gives_a_reason():
    for path, reason in benchmarks.EXEMPT.items():
        assert reason and len(reason) > 8, f"{path} needs a real reason"
        assert os.path.exists(os.path.join(ROOT, path)), f"{path} does not exist"


def test_the_registry_lists_every_benchmark_it_claims():
    names = [name for name, _function, _what in benchmarks.BENCHMARKS]
    assert names == ["blackboard", "claims", "registry", "address", "rerank",
                     "retrieval", "ingest", "extract", "serve", "index_delta",
                     "snapshot", "entities", "edges", "communities", "paths"]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("name", ["blackboard", "claims", "registry", "address",
                                  "rerank"])
def test_each_fast_benchmark_returns_numbers(name):
    """The fast ones are asserted here; `retrieval` is slow and runs in CI's
    benchmark step instead."""
    function = dict((entry[0], entry[1]) for entry in benchmarks.BENCHMARKS)[name]
    numbers = function()
    assert numbers, f"{name} returned nothing"
    assert all(isinstance(value, (int, float)) for value in numbers.values())
    assert all(value >= 0 for value in numbers.values())
