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
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/backends":
            return self._send(200, {"backends": router.report()})
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
