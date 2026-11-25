"""The harvester: get the bytes, obey the rules, turn them into text.

Three jobs, in this order:

  1. Permission. robots.txt is fetched once per host and cached. A disallowed
     path is never requested. A robots.txt that cannot be read means the host
     is treated as disallowed, because failing open is how a crawler gets
     banned.
  2. Politeness. One request per host per `http.rate_per_host` seconds, a
     timeout, a byte cap, and a user agent that says who it is.
  3. Extraction. HTML becomes text with the navigation removed, links kept for
     the crawl frontier, and the raw byte count kept so the quality gate can
     measure how much of the page was content.

`harvest_local` does the same job for files on disk, which is how the whole
pipeline is testable without a network.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional

# Tags whose text is never content.
SKIP_TAGS = {
    "script", "style", "noscript", "nav", "header", "footer", "aside", "form",
    "svg", "iframe", "template", "button", "select", "option",
}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre", "table", "ul", "ol", "dl", "dd", "dt",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


@dataclass
class Document:
    """One harvested unit of source material."""

    url: str
    title: str
    text: str
    retrieved: str = field(default_factory=now_utc)
    domain: str = ""
    raw_chars: int = 0
    status: int = 0
    content_hash: str = ""
    licence: Optional[str] = None
    kind: str = "prose"           # prose | code
    lang: str = "en"
    links: List[str] = field(default_factory=list)
    error: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = host_of(self.url)
        if not self.raw_chars:
            self.raw_chars = len(self.text)
        if not self.content_hash:
            self.content_hash = sha256_text(self.text)

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.text.strip()) and not self.error

    def to_manifest(self) -> dict:
        return {
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "retrieved": self.retrieved,
            "raw_chars": self.raw_chars,
            "chars": len(self.text),
            "status": self.status,
            "content_hash": self.content_hash,
            "licence": self.licence,
            "kind": self.kind,
            "lang": self.lang,
            "links": len(self.links),
            "error": self.error,
        }


def host_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return parsed.netloc.lower().split(":")[0]
    if url.startswith("file://"):
        return "local"
    return ""


_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_url(url: str) -> str:
    """Accept a bare path, a bare host, or a full URL.

    `vrusha harvest --seed ./notes.html` should work as well as a full URL:
    the first is how you test, the second is how you run.
    """
    value = (url or "").strip()
    if not value:
        return value
    if _SCHEME.match(value):
        return value
    if os.path.exists(value):
        return "file://" + os.path.abspath(value)
    return "https://" + value


# --------------------------------------------------------------------------
# HTML -> text
# --------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.canonical = ""
        self.links: List[str] = []
        self._parts: List[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "pre":
            self._in_pre = True
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "link" and (attrs.get("rel") or "").lower() == "canonical":
            self.canonical = attrs.get("href", "")
        if tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag == "pre":
            self._in_pre = False
        if tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if self._in_pre:
            self._parts.append(data)
        else:
            self._parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        out: List[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()


def html_to_text(html: str, base_url: str = "") -> tuple[str, str, List[str]]:
    """Return (title, text, absolute links). Never raises on bad markup."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # a malformed page is still a page; keep whatever was parsed
        pass
    links = []
    for href in parser.links:
        href = (href or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        links.append(urllib.parse.urljoin(base_url, href) if base_url else href)
    title = re.sub(r"\s+", " ", parser.title).strip()
    return title, parser.text(), links


# --------------------------------------------------------------------------
# politeness
# --------------------------------------------------------------------------

class RateLimiter:
    """At most one request per host per interval."""

    def __init__(self, interval: float = 1.0):
        self.interval = max(0.0, interval)
        self._last: Dict[str, float] = {}

    def wait(self, host: str) -> None:
        if not self.interval:
            return
        last = self._last.get(host, 0.0)
        delta = time.monotonic() - last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last[host] = time.monotonic()


class RobotsGate:
    """robots.txt, cached per host, fail-closed."""

    def __init__(self, user_agent: str, timeout: float = 10.0, respect: bool = True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect = respect
        self._cache: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    def _parser(self, scheme: str, host: str):
        if host in self._cache:
            return self._cache[host]
        parser = urllib.robotparser.RobotFileParser()
        url = f"{scheme}://{host}/robots.txt"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(200_000).decode("utf-8", errors="ignore")
            parser.parse(body.splitlines())
            self._cache[host] = parser
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._cache[host] = None          # exists but forbids us
            elif 400 <= exc.code < 500:
                parser.parse([])                  # no robots.txt: everything allowed
                self._cache[host] = parser
            else:
                self._cache[host] = None          # 5xx: do not hammer, fail closed
        except Exception:
            self._cache[host] = None              # network trouble: fail closed
        return self._cache[host]

    def allowed(self, url: str) -> tuple[bool, str]:
        if not self.respect:
            return True, "robots disabled by config"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True, "not an http url"
        parser = self._parser(parsed.scheme, parsed.netloc)
        if parser is None:
            return False, "robots.txt unreadable or forbidding; treating as disallowed"
        if parser.can_fetch(self.user_agent, url):
            return True, "allowed by robots.txt"
        return False, "disallowed by robots.txt"


# --------------------------------------------------------------------------
# the fetcher
# --------------------------------------------------------------------------

class Fetcher:
    """One instance per run: shares the rate limiter and the robots cache."""

    def __init__(self, cfg: dict):
        http = cfg["http"]
        self.cfg = cfg
        self.user_agent = http["user_agent"]
        self.timeout = float(http["timeout"])
        self.max_bytes = int(http["max_bytes"])
        self.max_redirects = int(http.get("max_redirects", 5))
        self.limiter = RateLimiter(float(http.get("rate_per_host", 1.0)))
        self.robots = RobotsGate(self.user_agent, self.timeout, bool(http.get("respect_robots", True)))
        self._seen: set[str] = set()

    # -- one page ------------------------------------------------------
    def get(self, url: str) -> Document:
        url = normalize_url(url)
        allowed, why = self.robots.allowed(url)
        if not allowed:
            return Document(url=url, title="", text="", status=0, error=f"robots: {why}")

        host = host_of(url)
        self.limiter.wait(host)
        request = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes)
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                if encoding == "gzip":
                    raw = gzip.decompress(raw)
                elif encoding == "deflate":
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                charset = response.headers.get_content_charset() or "utf-8"
                # file:// responses carry status None rather than 200
                status = getattr(response, "status", None) or getattr(response, "code", None) or 200
                final_url = response.geturl() or url
        except urllib.error.HTTPError as exc:
            return Document(url=url, title="", text="", status=exc.code,
                            error=f"HTTP {exc.code} {exc.reason}")
        except Exception as exc:
            return Document(url=url, title="", text="", status=0, error=f"{type(exc).__name__}: {exc}")

        try:
            html = raw.decode(charset, errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")

        title, text, links = html_to_text(html, final_url)
        if not title:
            first = next((line for line in text.splitlines() if line.strip()), "")
            title = first[:120]
        return Document(
            url=final_url,
            title=title,
            text=text,
            raw_chars=len(raw),
            status=status,
            links=links,
        )

    # -- many pages ----------------------------------------------------
    def crawl(self, seeds: List[str], max_pages: int = 20, max_depth: int = 2,
              same_domain: bool = True, allowed_host: Optional[str] = None) -> List[Document]:
        """Breadth-first over the seeds. Polite by construction, not by hope."""
        seeds = [normalize_url(s) for s in seeds]
        frontier: List[tuple[str, int]] = [(s, 0) for s in seeds]
        docs: List[Document] = []
        seen: set[str] = set()
        root_host = allowed_host or (host_of(seeds[0]) if seeds else "")
        while frontier and len(docs) < max_pages:
            url, depth = frontier.pop(0)
            key = canonical_key(url)
            if key in seen:
                continue
            seen.add(key)
            if same_domain and root_host and host_of(url) != root_host:
                continue
            doc = self.get(url)
            docs.append(doc)
            if not doc.ok or depth >= max_depth:
                continue
            for link in doc.links:
                if len(frontier) + len(docs) >= max_pages * 3:
                    break
                if host_of(link) and canonical_key(link) not in seen:
                    if not same_domain or host_of(link) == root_host:
                        frontier.append((link, depth + 1))
        return docs


def canonical_key(url: str) -> str:
    """Drop the fragment and the trailing slash so one page is one key."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


# --------------------------------------------------------------------------
# local harvesting: the offline path, and the one the tests use
# --------------------------------------------------------------------------

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".jsonl", ".yaml", ".yml",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".go", ".rs", ".sql", ".sh", ".html", ".htm", ".css",
}


def harvest_local(path: str, licence: Optional[str] = None) -> List[Document]:
    """Read a file or a directory tree. HTML is extracted, everything else kept."""
    targets: List[str] = []
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"})
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in TEXT_SUFFIXES:
                    targets.append(os.path.join(root, name))
    else:
        targets = [path]

    docs: List[Document] = []
    for target in targets:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError as exc:
            docs.append(Document(url=f"file://{target}", title=os.path.basename(target),
                                 text="", status=0, error=str(exc)))
            continue
        suffix = os.path.splitext(target)[1].lower()
        url = "file://" + os.path.abspath(target)
        if suffix in (".html", ".htm"):
            title, text, _ = html_to_text(raw, url)
            kind = "prose"
        else:
            title = os.path.basename(target)
            text = raw
            kind = "code" if suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c",
                                        ".h", ".cpp", ".hpp", ".go", ".rs", ".sql", ".sh",
                                        ".css"} else "prose"
        docs.append(Document(
            url=url, title=title or os.path.basename(target), text=text,
            raw_chars=len(raw.encode("utf-8")), licence=licence, kind=kind,
            status=200,
        ))
    return docs


def write_manifest(docs: List[Document], directory: str, name: Optional[str] = None) -> str:
    """Append one JSONL row per document. Returns the file path."""
    os.makedirs(directory, exist_ok=True)
    name = name or f"harvest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    path = os.path.join(directory, name)
    with open(path, "a", encoding="utf-8") as fh:
        for doc in docs:
            fh.write(json.dumps(doc.to_manifest(), ensure_ascii=False) + "\n")
    return path


def read_manifests(directory: str) -> List[dict]:
    rows: List[dict] = []
    if not os.path.isdir(directory):
        return rows
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return rows
