"""Web search: five backends behind one function.

    backend    needs            what it is
    ---------  ---------------  ------------------------------------------------
    tavily     TAVILY_API_KEY   a paid search API built for LLM pipelines
    brave      BRAVE_API_KEY    an independent index
    searxng    SEARXNG_URL      your own meta-search instance, no key
    ddg        nothing          the DuckDuckGo HTML endpoint
    fixtures   nothing          a local JSON file of results (offline, tests)
    store      a database       search what you already harvested

`auto` tries them in that order and falls through on any failure, so a missing
key is never an error — it is a step down the ladder. Every backend returns the
same `SearchHit`, so the harvester never learns which one answered.

The fixtures backend is not a toy: it is how the whole pipeline is tested and
demoed without a network, and how you replay a search that mattered.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

FIXTURE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples", "fixtures", "search",
)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    score: float = 0.0
    source: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet,
                "score": self.score, "source": self.source}


class BackendError(RuntimeError):
    """A backend was asked to run and could not. `auto` catches these."""


class Backend:
    name = "base"
    needs_key = False

    def available(self) -> bool:
        return True

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# fixtures: offline, deterministic, replayable
# --------------------------------------------------------------------------

class FixtureBackend(Backend):
    """Reads examples/fixtures/search/*.json and ranks by word overlap."""

    name = "fixtures"

    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or FIXTURE_DIR

    def available(self) -> bool:
        return os.path.isdir(self.directory)

    def _load(self) -> List[dict]:
        rows: List[dict] = []
        if not os.path.isdir(self.directory):
            return rows
        for name in sorted(os.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.directory, name), "r", encoding="utf-8") as fh:
                try:
                    doc = json.load(fh)
                except json.JSONDecodeError:
                    continue
                if isinstance(doc, dict):
                    rows.extend(doc.get("results", []))
                elif isinstance(doc, list):
                    rows.extend(doc)
        return rows

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        terms = {w for w in re.findall(r"[a-z0-9]{3,}", query.lower())}
        scored: List[SearchHit] = []
        for row in self._load():
            body = f"{row.get('title','')} {row.get('snippet','')} {row.get('url','')}".lower()
            words = set(re.findall(r"[a-z0-9]{3,}", body))
            overlap = len(terms & words)
            if terms and overlap == 0:
                continue
            scored.append(SearchHit(
                title=row.get("title", ""),
                url=row.get("url", ""),
                snippet=row.get("snippet", ""),
                score=float(overlap),
                source="fixtures",
            ))
        scored.sort(key=lambda h: (-h.score, h.url))
        return scored[:limit]


# --------------------------------------------------------------------------
# DuckDuckGo HTML endpoint
# --------------------------------------------------------------------------

class DuckDuckGoBackend(Backend):
    name = "ddg"

    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, user_agent: str, timeout: float = 15.0):
        self.user_agent = user_agent
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        data = urllib.parse.urlencode({"q": query}).encode()
        request = urllib.request.Request(
            self.ENDPOINT, data=data,
            headers={"User-Agent": self.user_agent,
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                html = response.read(600_000).decode("utf-8", errors="replace")
        except Exception as exc:
            raise BackendError(f"duckduckgo: {type(exc).__name__}: {exc}") from exc
        hits: List[SearchHit] = []
        # result blocks: <a class="result__a" href="...">title</a>
        for match in re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        ):
            url = urllib.parse.unquote(match.group(1))
            if "uddg=" in url:
                url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("uddg", [url])[0]
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            hits.append(SearchHit(title=title, url=url, snippet="",
                                  score=1.0 / (1 + len(hits)), source="ddg"))
            if len(hits) >= limit:
                break
        if not hits:
            raise BackendError("duckduckgo: no results parsed (endpoint may be throttling)")
        return hits


# --------------------------------------------------------------------------
# SearXNG (self-hosted, no key)
# --------------------------------------------------------------------------

class SearxngBackend(Backend):
    name = "searxng"

    def __init__(self, base_url: str, user_agent: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.base_url)

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if not self.base_url:
            raise BackendError("searxng: SEARXNG_URL is not set")
        url = f"{self.base_url}/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json"})
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                doc = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception as exc:
            raise BackendError(f"searxng: {type(exc).__name__}: {exc}") from exc
        hits = [
            SearchHit(title=row.get("title", ""), url=row.get("url", ""),
                      snippet=row.get("content", ""), score=float(row.get("score", 0.0)),
                      source="searxng")
            for row in doc.get("results", [])
        ]
        return hits[:limit]


# --------------------------------------------------------------------------
# Tavily and Brave (keyed)
# --------------------------------------------------------------------------

class TavilyBackend(Backend):
    name = "tavily"
    needs_key = True

    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if not self.api_key:
            raise BackendError("tavily: TAVILY_API_KEY is not set")
        payload = json.dumps({
            "api_key": self.api_key, "query": query,
            "max_results": limit, "search_depth": "basic",
        }).encode()
        request = urllib.request.Request(
            "https://api.tavily.com/search", data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                doc = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception as exc:
            raise BackendError(f"tavily: {type(exc).__name__}: {exc}") from exc
        return [
            SearchHit(title=row.get("title", ""), url=row.get("url", ""),
                      snippet=row.get("content", ""), score=float(row.get("score", 0.0)),
                      source="tavily")
            for row in doc.get("results", [])
        ][:limit]


class BraveBackend(Backend):
    name = "brave"
    needs_key = True

    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if not self.api_key:
            raise BackendError("brave: BRAVE_API_KEY is not set")
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": query, "count": limit})
        request = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                doc = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception as exc:
            raise BackendError(f"brave: {type(exc).__name__}: {exc}") from exc
        rows = (doc.get("web") or {}).get("results", [])
        return [
            SearchHit(title=row.get("title", ""), url=row.get("url", ""),
                      snippet=row.get("description", ""), score=0.0, source="brave")
            for row in rows
        ][:limit]


# --------------------------------------------------------------------------
# the local store as a backend
# --------------------------------------------------------------------------

class StoreBackend(Backend):
    """Search what you already harvested. Cheapest possible answer."""

    name = "store"

    def __init__(self, store):
        self.store = store

    def available(self) -> bool:
        return self.store is not None

    def search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if self.store is None:
            raise BackendError("store: no database")
        rows = self.store.search_documents(query, limit=limit)
        return [
            SearchHit(title=row.get("title", ""), url=row.get("url", ""),
                      snippet=(row.get("snippet") or "")[:300],
                      score=float(row.get("score", 0.0)), source="store")
            for row in rows
        ]


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def build_backend(name: str, cfg: dict, store=None, fixture_dir: Optional[str] = None) -> Backend:
    http = cfg.get("http", {})
    ua = http.get("user_agent", "vrusha/0.1")
    timeout = float(http.get("timeout", 20.0))
    table: Dict[str, Backend] = {
        "fixtures": FixtureBackend(fixture_dir),
        "ddg": DuckDuckGoBackend(ua, timeout),
        "searxng": SearxngBackend(os.environ.get("SEARXNG_URL", ""), ua, timeout),
        "tavily": TavilyBackend(os.environ.get("TAVILY_API_KEY", ""), timeout),
        "brave": BraveBackend(os.environ.get("BRAVE_API_KEY", ""), timeout),
        "store": StoreBackend(store),
    }
    if name not in table:
        raise BackendError(f"unknown search backend {name!r}; have {sorted(table)}")
    return table[name]


AUTO_ORDER = ["tavily", "brave", "searxng", "ddg", "fixtures"]


def search(query: str, limit: int = 10, cfg: Optional[dict] = None, backend: Optional[str] = None,
           store=None, fixture_dir: Optional[str] = None) -> tuple[List[SearchHit], List[str]]:
    """Search, falling down the ladder in `auto`. Returns (hits, notes)."""
    cfg = cfg or {"http": {}}
    name = backend or cfg.get("harvest", {}).get("backend", "auto")
    notes: List[str] = []
    if name != "auto":
        hits = build_backend(name, cfg, store, fixture_dir).search(query, limit)
        return hits, [f"{name}: {len(hits)} results"]
    for candidate in AUTO_ORDER:
        impl = build_backend(candidate, cfg, store, fixture_dir)
        if not impl.available():
            notes.append(f"{candidate}: not configured")
            continue
        try:
            hits = impl.search(query, limit)
        except BackendError as exc:
            notes.append(str(exc))
            continue
        if hits:
            notes.append(f"{candidate}: {len(hits)} results")
            return hits, notes
        notes.append(f"{candidate}: 0 results")
    return [], notes
