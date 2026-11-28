"""The unified JSON API: one port, either engine behind it.

    POST /search   {text|vector, k, filter?, backend?}  -> hits, backend, reason
    POST /ask      {question, k?}                       -> answer + citations
    GET  /backends                                      -> health, EWMA, capabilities
    GET  /health   /stats                               -> liveness, corpus

The point of the endpoint is not to expose HTTP for its own sake. It is that
the decision lives in one process: `vrusha serve` holds a single `Router`, so
its EWMA latency and its failover state survive between requests. A one-shot
`vrusha query` starts cold every time and therefore always begins at the
default backend.

Zero dependencies: `http.server` and `json`.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import numpy as np

from .config import resolve
from .store.backends import build_backends
from .store.db import Store
from .store.router import Router


class _Handler(BaseHTTPRequestHandler):
    server_version = "vrusha/0.2"

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):      # keep the CLI quiet
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes ------------------------------------------------------------

    def do_GET(self):                        # noqa: N802 - stdlib naming
        router: Router = self.server.router
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            # a route index, because a JSON API nobody can discover is a JSON
            # API nobody uses
            return self._send(200, {
                "service": "vrusha",
                "routes": {
                    "GET /": "this index",
                    "GET /health": "liveness",
                    "GET /stats": "vectors, dimension, backends",
                    "GET /backends": "every vector engine and its state",
                    "GET /tables": "extracted tables",
                    "GET /tables/<id>": "one table, with its rows",
                    "GET /entities": "resolved entity groups",
                    "GET /tools": "dubforge, forge3d, cutforge: ready or not",
                    "POST /search": "{text|vector, k, filter} -> ranked chunk ids",
                    "POST /ask": "{question, k} -> answer with citations",
                    "POST /math": "{expression} -> stored formulae that match",
                },
                "not_here": {
                    "research": "it fetches the web; run it as a command, not a "
                                "request that a client has to wait on",
                },
            })
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/backends":
            return self._send(200, {"backends": router.report()})
        if path == "/tables":
            return self._tables()
        if path.startswith("/tables/"):
            return self._table(path.rsplit("/", 1)[-1])
        if path == "/entities":
            return self._entities()
        if path == "/tools":
            from .tools import describe_all
            return self._send(200, {"tools": describe_all()})
        if path == "/stats":
            rows, _max = self.server.store.vector_fingerprint()
            return self._send(200, {"vectors": rows,
                                    "dim": self.server.store.vector_dim(),
                                    "backends": router.report()})
        return self._send(404, {"error": f"no route {path}"})

    def do_POST(self):                       # noqa: N802
        router: Router = self.server.router
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            body = self._body()
        except ValueError as exc:
            return self._send(400, {"error": f"bad json: {exc}"})

        if path == "/search":
            return self._search(router, body)
        if path == "/ask":
            return self._ask(body)
        if path == "/math":
            return self._math(body)
        return self._send(404, {"error": f"no route {path}"})

    def _search(self, router: Router, body: dict):
        text = str(body.get("text") or "")
        k = int(body.get("k") or 8)
        filter_spec = body.get("filter") or None
        vector = body.get("vector")
        if vector is not None:
            vector = np.asarray(vector, dtype=np.float32)
        elif text:
            vector = router.embedder.encode_one(text)
        else:
            return self._send(400, {"error": "send text or vector"})

        hits, decision, timings = router.search(
            vector, k, text=text, filter=filter_spec)
        return self._send(200, {
            "backend": decision.backend,
            "reason": decision.reason,
            "gate": decision.gate,
            "skipped": decision.skipped,
            "ms": timings,
            "hits": [{"chunk_id": chunk_id, "score": round(score, 4)}
                     for chunk_id, score in hits],
        })

    def _tables(self):
        from .store.records import list_tables
        return self._send(200, {"tables": list_tables(self.server.store)})

    def _table(self, ident: str):
        from .store.records import table_rows
        try:
            table_id = int(ident)
        except ValueError:
            return self._send(400, {"error": f"not a table id: {ident!r}"})
        rows = table_rows(self.server.store, table_id, limit=500)
        if not rows:
            return self._send(404, {"error": f"no table {table_id}"})
        return self._send(200, {"table_id": table_id, "rows": rows})

    def _entities(self):
        from .store import entities
        groups = entities.list_entities(self.server.store)
        return self._send(200, {"entities": groups,
                                "hint": None if groups else
                                "run `vrusha entities --build`"})

    def _math(self, body: dict):
        from .store.records import search_equations
        expression = str(body.get("expression") or "").strip()
        if not expression:
            return self._send(400, {"error": "send an expression"})
        hits = search_equations(self.server.store, expression,
                                limit=int(body.get("k") or 8))
        if not hits:
            return self._send(200, {"expression": expression, "matches": [],
                                    "hint": "no stored formula matches; "
                                            "`vrusha extract --dir <path>` reads them"})
        return self._send(200, {"expression": expression,
                                "matches": hits})

    def _ask(self, body: dict):
        from .store.query import retrieve
        question = str(body.get("question") or "").strip()
        if not question:
            return self._send(400, {"error": "send a question"})
        result = retrieve(self.server.store, self.server.embedder, question,
                          self.server.cfg, top_k=int(body.get("k") or 8))
        return self._send(200, {
            "question": question,
            "answer": getattr(result, "answer", "") or "",
            "hits": [{"chunk_id": hit.chunk_id, "citation": hit.citation(),
                      "score": round(hit.score, 4), "text": hit.text[:400]}
                     for hit in result.hits],
        })


def serve(cfg: dict, embedder, *, host: str = "127.0.0.1",
          port: int = 8000, verbose: bool = False) -> ThreadingHTTPServer:
    """Build the server, owning its own store.

    It opens the database with `check_same_thread=False` itself rather than
    accepting one: a threaded server handed a connection made in another thread
    fails every request with a `ProgrammingError`, and the failure looks like
    "no backend available" rather than "wrong connection".
    """
    store = Store(resolve(cfg, "db"), check_same_thread=False)
    router = Router(store, embedder, cfg, build_backends(store, cfg, embedder))
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.router = router            # type: ignore[attr-defined]
    httpd.store = store              # type: ignore[attr-defined]
    httpd.embedder = embedder        # type: ignore[attr-defined]
    httpd.cfg = cfg                  # type: ignore[attr-defined]
    httpd.verbose = verbose          # type: ignore[attr-defined]
    return httpd


def serve_in_thread(cfg: dict, embedder, *, host="127.0.0.1",
                    port=8000) -> tuple:
    httpd = serve(cfg, embedder, host=host, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
