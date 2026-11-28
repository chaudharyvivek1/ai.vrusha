"""Remote agents: the same protocol over HTTP.

An L4 agent on another machine needs two things from the board — append and read —
and nothing else. This is the client for those two calls, and the server side is
two routes in `serve.py`.

**What breaks when a second machine joins, stated plainly.** `seq` is allocated by
the *server*, in one transaction, so it stays dense and monotonic while there is one
server. Add a second and `seq` is no longer global: two servers hand out 1, 2, 3
independently and the log has no single order. That is why `docs/AGENTS.md` §3 says
the blackboard is per-run and the log is append-only — a real log replaces this
server without any agent changing.

Until then: **one server, one writer, one order.** The client refuses to pretend
otherwise by never allocating a `seq` itself.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


class RemoteError(RuntimeError):
    """The server said no, or was not there."""


@dataclass
class RemoteBoard:
    """A blackboard on another machine, reached over HTTP."""

    url: str
    timeout: float = 10.0

    def _call(self, path: str, payload: Optional[dict] = None) -> dict:
        base = self.url.rstrip("/")
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=headers,
                                         method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RemoteError(f"{path}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RemoteError(f"{path}: {exc}") from exc

    def append(self, run: str, *, task: str = "", agent: str = "",
               kind: str = "finding", summary: str = "",
               refs: Optional[List[str]] = None,
               payload: Optional[dict] = None) -> dict:
        """Write one entry. The **server** allocates `seq`, never the client."""
        return self._call("/board", {"run": run, "task": task, "agent": agent,
                                     "kind": kind, "summary": summary,
                                     "refs": list(refs or []),
                                     "payload": payload or {}})

    def since(self, run: str, seq: int = 0, limit: int = 100) -> List[dict]:
        answer = self._call(f"/board?run={run}&since={int(seq)}&limit={int(limit)}")
        return list(answer.get("entries", []))

    def claim(self, run: str, task: str, agent: str, ttl: float = 60.0) -> dict:
        return self._call("/claim", {"run": run, "task": task, "agent": agent,
                                     "ttl": float(ttl)})

    def complete(self, run: str, task: str, agent: str,
                 status: str = "done") -> dict:
        return self._call("/complete", {"run": run, "task": task, "agent": agent,
                                        "status": status})

    def agents(self, run: str) -> dict:
        return self._call(f"/agents?run={run}")
