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
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from .charset import CharsetResult, decode, detect_charset, replacement_ratio
from .net import (
    REDIRECT_CODES,
    DecompressionError,
    NetworkError,
    RedirectError,
    ResponseTooLarge,
    UnsafeDestination,
    build_opener,
    canonical_host,
    check_scheme,
    decompress_capped,
    format_host,
    resolve_safe,
)

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
    #: The page as fetched, kept so tables and JSON-LD can be extracted from the
    #: markup rather than re-inferred from the flattened text.
    html: str = ""
    error: str = ""
    #: How the bytes were decoded, and how sure we are. Recorded in the
    #: manifest so a mis-decode is visible after the fact.
    encoding: str = ""
    encoding_source: str = ""
    encoding_confidence: float = 0.0
    encoding_lossy: bool = False

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
            "encoding": self.encoding,
            "encoding_source": self.encoding_source,
            "encoding_confidence": round(self.encoding_confidence, 2),
            "encoding_lossy": self.encoding_lossy,
        }


def host_of(url: str) -> str:
    """Best-effort hostname for display and statistics.

    Never raises: this is used for document metadata and for grouping. The
    security-relevant path uses `canonical_host`, which does raise, because a
    check that silently returns "" is a check that silently passes.
    """
    if (url or "").startswith("file://"):
        return "local"
    try:
        return canonical_host(url)
    except UnsafeDestination:
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


def html_to_text(html: str, base_url: str = "", extractor: str = "auto"
                 ) -> tuple[str, str, List[str]]:
    """Return (title, text, absolute links). Never raises on bad markup.

    `extractor`:
        simple       the built-in tag stripper: fast, no dependency. It drops
                     nav and footer by tag, but keeps a cookie banner inside the
                     body, because nothing in the markup says it is boilerplate
        trafilatura  boilerplate removal by a maintained extractor; the text is
                     what a reader would see, which is what a corpus wants
        auto         trafilatura when it is installed and returns something
                     substantial, the stripper otherwise. Not the default: the
                     text a corpus stores should not depend on what is installed

    The title and the links always come from the built-in parser, because
    trafilatura returns neither and both are needed downstream.
    """
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
    simple = parser.text()

    if extractor not in ("simple", "auto", "trafilatura"):
        raise ValueError(f"unknown extractor {extractor!r}; "
                         f"use simple, trafilatura or auto")
    if extractor != "simple":
        try:
            from trafilatura import extract as _trafilatura_extract
            cleaned = _trafilatura_extract(html, include_comments=False,
                                           include_tables=False, favor_recall=True)
        except Exception:                    # noqa: BLE001 - absent or unhappy
            cleaned = None
        if cleaned and len(cleaned) >= 200:
            return title, cleaned.strip(), links
        if extractor == "trafilatura" and not cleaned:
            # asked for it and got nothing: say so rather than silently
            # returning the stripper's output and calling it extracted
            raise RuntimeError("trafilatura returned no text for this page")
    return title, simple, links


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
    """robots.txt, cached per host, fail-closed.

    `opener` is the pinned opener from the fetcher. Without it, fetching
    robots.txt would resolve the hostname a second time, which is the DNS
    rebinding window the pinning exists to close.
    """

    def __init__(self, user_agent: str, timeout: float = 10.0, respect: bool = True,
                 opener=None):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect = respect
        self.opener = opener
        self._cache: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    def _parser(self, scheme: str, authority: str):
        """`authority` is host plus optional port, already bracketed for IPv6."""
        if authority in self._cache:
            return self._cache[authority]
        parser = urllib.robotparser.RobotFileParser()
        url = f"{scheme}://{authority}/robots.txt"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            if self.opener is not None:
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read(200_000).decode("utf-8", errors="ignore")
            else:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read(200_000).decode("utf-8", errors="ignore")
            parser.parse(body.splitlines())
            self._cache[authority] = parser
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._cache[authority] = None     # exists but forbids us
            elif 400 <= exc.code < 500:
                parser.parse([])                  # no robots.txt: everything allowed
                self._cache[authority] = parser
            else:
                self._cache[authority] = None     # 5xx: do not hammer, fail closed
        except Exception:
            self._cache[authority] = None         # network trouble: fail closed
        return self._cache[authority]

    def allowed(self, url: str) -> tuple[bool, str]:
        if not self.respect:
            return True, "robots disabled by config"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True, "not an http url"
        # The robots request must go to the same host the page request will,
        # built from the canonical hostname -- not from the raw netloc, which
        # still carries any userinfo and is where an IPv6 literal loses its
        # brackets.
        try:
            host = canonical_host(url)
            port = parsed.port
        except UnsafeDestination as exc:
            return False, str(exc)
        authority = format_host(host) + (f":{port}" if port else "")
        parser = self._parser(parsed.scheme, authority)
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

    def __init__(self, cfg: dict, allow_private: bool = False,
                 allow_hosts: Iterable[str] = ()):
        http = cfg["http"]
        self.cfg = cfg
        self.user_agent = http["user_agent"]
        self.timeout = float(http["timeout"])
        self.max_bytes = int(http["max_bytes"])
        self.max_compressed = int(http.get("max_compressed_bytes", self.max_bytes))
        self.max_decompressed = int(http.get("max_decompressed_bytes", 20_000_000))
        self.max_redirects = int(http.get("max_redirects", 5))
        #: Loopback and private destinations are refused.
        #: `allow_hosts` is the narrow exception: named hosts only, which is
        #: what a loopback test needs. `allow_private` opens the whole address
        #: class and exists only for a deliberately hostile test.
        self.allow_private = bool(allow_private or http.get("allow_private", False))
        self.allow_hosts = frozenset(str(h).strip("[]").lower() for h in (allow_hosts or ()))
        self.limiter = RateLimiter(float(http.get("rate_per_host", 1.0)))
        self.opener = build_opener(allow_private=self.allow_private,
                                   allow_hosts=self.allow_hosts)
        self.robots = RobotsGate(self.user_agent, self.timeout,
                                 bool(http.get("respect_robots", True)),
                                 opener=self.opener)
        self._seen: set[str] = set()

    # -- one page ------------------------------------------------------
    def get(self, url: str, allowed_host: Optional[str] = None) -> Document:
        """Fetch one page, following redirects by hand with a check per hop.

        `urllib` would follow a redirect for us and never look at where it
        landed. Here every hop goes through `_check_destination` (scheme,
        robots.txt, permitted host) and the connection layer (address
        validation and pinning) before a socket is opened.
        """
        try:
            return self._get_checked(normalize_url(url), allowed_host)
        except NetworkError as exc:
            return Document(url=url, title="", text="", status=0,
                            error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:                 # pragma: no cover - defensive
            return Document(url=url, title="", text="", status=0,
                            error=f"{type(exc).__name__}: {exc}")

    def _get_checked(self, url: str, allowed_host: Optional[str]) -> Document:
        visited: List[str] = [canonical_key(url)]
        current = url
        for hop in range(self.max_redirects + 1):
            self._check_destination(current, allowed_host, first=(hop == 0))
            status, headers, body, final_url = self._fetch_once(current)
            if status in REDIRECT_CODES:
                location = headers.get("Location") if headers else None
                if not location:
                    raise RedirectError(f"{current} answered {status} with no Location")
                target = urllib.parse.urljoin(final_url, location)
                key = canonical_key(target)
                if key in visited:
                    raise RedirectError(
                        f"redirect loop: {target} already visited in this chain")
                visited.append(key)
                current = target
                continue
            return self._build_document(final_url, status, headers, body)
        raise RedirectError(
            f"more than {self.max_redirects} redirects starting at {url}")

    def _check_destination(self, url: str, allowed_host: Optional[str],
                           first: bool = True) -> None:
        """Every hop passes this before any socket is opened.

        Order matters. The address is validated *before* robots.txt is fetched,
        because fetching robots.txt is itself a request to the host: checking it
        first would mean connecting to 169.254.169.254 in order to ask whether
        we may connect to 169.254.169.254.

        `file` is allowed only for the first hop. A remote page must never be
        able to redirect the harvester into reading a local file -- that turns
        one permitted URL into `/etc/passwd`.
        """
        scheme = check_scheme(url)               # raises UnsafeDestination
        if scheme == "file":
            if not first:
                raise UnsafeDestination(
                    "a redirect may not target file://; local files are read only "
                    "when they are the requested source, never as a destination")
            return

        # `canonical_host` raises on credentials, a missing host, a bad port or
        # a hostname that is not valid IDNA. Every one of those is a URL that
        # cannot be safely compared against a permitted host.
        host = canonical_host(url)
        # Policy before network: leaving the permitted host needs no DNS lookup
        # to detect, and asking for one would leak the hostname of a page we
        # are about to refuse.
        if allowed_host and host != allowed_host.lower():
            raise RedirectError(f"{url} leaves the permitted host {allowed_host!r}")

        port = urlsplit(url).port or (443 if scheme == "https" else 80)
        resolve_safe(host, port, allow_private=self.allow_private,
                     allow_hosts=self.allow_hosts)

        allowed, why = self.robots.allowed(url)
        if not allowed:
            raise UnsafeDestination(f"robots: {why}")

    def _fetch_once(self, url: str):
        """One request. No redirect is followed here; the caller decides."""
        host = host_of(url)
        self.limiter.wait(host)
        request = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in REDIRECT_CODES:
                headers = exc.headers
                exc.close()
                return exc.code, headers, b"", url
            raise NetworkError(f"HTTP {exc.code} {exc.reason}") from exc

        try:
            with response:
                status = (getattr(response, "status", None)
                          or getattr(response, "code", None) or 200)
                headers = response.headers
                final_url = response.geturl() or url
                raw = self._read_capped(response)
        except NetworkError:
            raise
        except Exception as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

        encoding = (headers.get("Content-Encoding") or "").lower() if headers else ""
        body = decompress_capped(raw, encoding, self.max_decompressed)
        return status, headers, body, final_url

    def _read_capped(self, response) -> bytes:
        """Read the body, refusing to buffer more than the compressed cap."""
        raw = bytearray()
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            raw += chunk
            if len(raw) > self.max_compressed:
                raise ResponseTooLarge(
                    f"response body exceeded {self.max_compressed} bytes (compressed)")
        return bytes(raw)

    def _build_document(self, url: str, status: int, headers, body: bytes) -> Document:
        header_charset = headers.get_content_charset() if headers else None
        result: CharsetResult = detect_charset(body, header_charset)
        html, lossy = decode(body, result)
        extractor = str((self.cfg.get("harvest") or {}).get("extractor", "simple"))
        title, text, links = html_to_text(html, url, extractor=extractor)
        if not title:
            first = next((line for line in text.splitlines() if line.strip()), "")
            title = first[:120]
        return Document(
            url=url,
            title=title,
            text=text,
            html=html,
            raw_chars=len(body),
            status=status,
            links=links,
            encoding=result.encoding,
            encoding_source=result.source,
            encoding_confidence=result.confidence,
            encoding_lossy=lossy or result.lossy,
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
            doc = self.get(url, allowed_host=root_host if same_domain else None)
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
