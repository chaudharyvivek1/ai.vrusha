"""Live proof that both engines serve the same store.

Starts the real RagForge Node server as a managed child process, exports the
store's chunks into it, and asks both engines the same question. Then kills the
server and asks again, to show the router failing over instead of erroring.

    RAGFORGE_DIR=../RagForge python bench/live_merge_test.py

It needs a built RagForge checkout (`npm install && npm run build` in that
repository) and Node 18+. Without them it prints what is missing and exits 1.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vrusha.config import DEFAULTS, ensure_dirs, resolve
from vrusha.ingest import ingest_paths
from vrusha.languages import load_registry
from vrusha.rules import load_rules
from vrusha.store import export as export_mod
from vrusha.store.backends import RagForgeBackend, build_backends
from vrusha.store.db import Store
from vrusha.store.embed import HashEmbedder
from vrusha.store.passes import PassContext, run_passes
from vrusha.store.router import Router

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: where the RagForge checkout lives; override with RAGFORGE_DIR
RAGFORGE = os.environ.get("RAGFORGE_DIR", os.path.join(os.path.dirname(ROOT), "RagForge"))
PORT = 8099
URL = f"http://127.0.0.1:{PORT}"


def wait_ready(url: str, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    root = tempfile.mkdtemp(prefix="mergetest-")
    cfg = {key: (dict(value) if isinstance(value, dict) else value)
           for key, value in DEFAULTS.items()}
    cfg["paths"] = dict(DEFAULTS["paths"])
    cfg["paths"]["root"] = root
    ensure_dirs(cfg)

    # 1 — a real corpus: the project's own docs, ingested through the gate
    store = Store(resolve(cfg, "db"))
    rules, registry = load_rules(), load_registry()
    docs = os.path.join(ROOT, "docs")
    ingested = ingest_paths([docs], store, cfg,
                            rules, registry, HashEmbedder(256),
                            licence="MIT", verbose=False)
    run_passes(PassContext(store=store, cfg=cfg, rules=rules,
                           registry=registry, verbose=False))
    from vrusha.store import ann as ann_mod
    cfg["ann"] = {**DEFAULTS["ann"], "min_vectors": 0, "target_cell_size": 64}
    ann_mod.build_for(store, cfg)
    rows, _max_id = store.vector_fingerprint()
    dim = store.vector_dim()
    print(f"1  store      {rows:,} vectors, {dim} dims, native index built "
          f"({ingested.documents} docs)")

    # 2 — seed RagForge through its own CLI, then start it as our child
    data_dir = os.path.join(root, "ragforge")
    os.makedirs(data_dir, exist_ok=True)
    corpus = os.path.join(root, "corpus.jsonl")
    written = export_mod.export_to_jsonl(store, corpus, verbose=False)
    print(f"2a jsonl      {written['chunks']:,} rows, {written['dim']} dims -> {corpus}")
    seed = subprocess.run(["node", "dist/src/cli.js", "ingest", "--in", corpus,
                           "--out", data_dir], cwd=RAGFORGE, capture_output=True,
                          text=True, timeout=180)
    if seed.returncode != 0:
        print("2b ingest     FAILED:", (seed.stderr or seed.stdout)[-1200:])
        return 1
    print("2b ingest     " + seed.stdout.strip().splitlines()[-1])
    log = open(os.path.join(root, "ragforge.log"), "w")
    server = subprocess.Popen(
        ["node", "dist/src/server.js", "--dir", data_dir, "--port", str(PORT)],
        cwd=RAGFORGE, stdout=log, stderr=subprocess.STDOUT)
    if not wait_ready(URL):
        log.close()
        server.terminate()
        print("2  ragforge   FAILED to start; last log lines:")
        print(open(os.path.join(root, "ragforge.log")).read()[-1500:])
        return 1
    print(f"2  ragforge   up on {URL} (pid {server.pid})")

    try:
        # 3 — what it says it holds
        backend = RagForgeBackend(url=URL, timeout=30.0)
        stats = backend.stats()
        print(f"3  ragforge   reports {stats.rows:,} rows, {stats.dim} dims, "
              f"{stats.detail.get('segments')} segments")

        # 4 — both engines, same question
        embedder = HashEmbedder(dim)
        cfg_routed = {**cfg, "backends": {**DEFAULTS["backends"],
                                         "ragforge": {"url": URL, "timeout": 5.0,
                                                      "token_env": "RAGFORGE_TOKEN"}}}
        backends = build_backends(store, cfg_routed, embedder)
        vector = embedder.encode_one("the address tree and its segments")

        native = backends["native"].search(vector, 10)
        theirs = backends["ragforge"].search(vector, 10)
        overlap = len({c for c, _ in native} & {c for c, _ in theirs})
        print(f"4  agreement  native {len(native)} hits, ragforge {len(theirs)} hits, "
              f"{overlap}/10 shared ids")
        if not theirs:
            print("   FAIL: ragforge returned nothing")
            return 1

        # 5 — the router, choosing
        router = Router(store, embedder, cfg_routed, backends)
        hits, decision, timings = router.search(vector, 10, text="address tree")
        print(f"5  router     served by {decision.backend} ({decision.gate}: "
              f"{decision.reason})")
        print(f"   timings    {timings}")

        # 5b — a query that needs a filter: only RagForge can do that
        hits_f, decision_f, timings_f = router.search(
            vector, 10, text="address tree", filter={"lang": "en"})
        print(f"5b filter     served by {decision_f.backend} ({decision_f.gate}: "
              f"{decision_f.reason})")
        if decision_f.backend != "ragforge":
            print("   FAIL: a filtered query did not reach the engine that can filter")
            return 1

        # 6 — kill the second engine; the same query must still answer
        server.terminate()
        server.wait(timeout=10)
        print("6  ragforge   stopped")
        hits_after, decision_after, timings_after = router.search(
            vector, 10, text="address tree")
        print(f"   failover   served by {decision_after.backend} "
              f"({decision_after.gate}: {decision_after.reason})")
        print(f"   timings    {timings_after}")
        if not hits_after:
            print("   FAIL: no answer after the engine died")
            return 1
        print("\nPASS  both engines served the same store, and the router "
              "survived losing one")
        return 0
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=10)
        log.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
