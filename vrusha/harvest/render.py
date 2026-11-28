"""JavaScript-rendered pages: a seam, and a refusal to pay for it by default.

`harvest` fetches HTML and reads it. A page that builds its content in JavaScript
returns a shell — a `<div id="root">` and a script tag — and the pipeline stores
nothing useful. This is the one retrieval gap named in the outside review that is
still open.

A headless browser is a **300 MB dependency with a system binary**, which is why it
is not the default:

- **`NullRenderer`** is the default and always declines, so `pip install vrusha`
  stays small and a machine without Chromium behaves exactly as before;
- **`PlaywrightRenderer`** is optional, imported only when chosen, and its absence
  is a clear message rather than a traceback;
- **detection is free** — a cheap ratio of visible text to markup, not a heuristic
  that runs a browser to decide whether to run a browser.

The honest limit: a page can look static and still need JavaScript, and a page can
look empty and be genuinely empty. The ratio catches the common case (an SPA shell)
and the work log says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol

#: Below this ratio of visible text to markup, a page is probably a shell.
#: Measured on the SPA shape: `<div id="root"></div><script src=...>` is ~0.0.
SHELL_RATIO = 0.02

#: A page shorter than this cannot be judged by ratio at all.
MIN_MARKUP = 200

_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


class Renderer(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def render(self, url: str, *, timeout: float = 20.0) -> Optional[str]:
        ...


def visible_text(html: str) -> str:
    """Text a reader would see, with script and style bodies removed."""
    if not html:
        return ""
    without = _SCRIPT.sub(" ", html)
    return re.sub(r"\s+", " ", _TAGS.sub(" ", without)).strip()


def looks_like_a_shell(html: str) -> bool:
    """Cheap, and free: no browser is started to decide this.

    A shell is mostly markup with almost no text — the shape every single-page app
    has before its JavaScript runs.
    """
    if not html or len(html) < MIN_MARKUP:
        return False
    text = visible_text(html)
    return (len(text) / max(1, len(html))) < SHELL_RATIO


@dataclass
class NullRenderer:
    """The default. Declines, and says why, so the fallback is not a mystery."""

    name: str = "none"

    def available(self) -> bool:
        return False

    def render(self, url: str, *, timeout: float = 20.0) -> Optional[str]:
        return None


@dataclass
class PlaywrightRenderer:
    """Chromium through Playwright. Optional, and unmeasured on this machine.

    Not adopted on evidence — adopted as a seam. `docs/work/part-36-render.md`
    records that the browser was not installed here, so its cost and its success
    rate are unknown, and the default stays `none` until someone measures them.
    """

    name: str = "playwright"
    timeout: float = 20.0

    def available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:                # noqa: BLE001 - optional dependency
            return False

    def render(self, url: str, *, timeout: float = 0.0) -> Optional[str]:
        if not self.available():
            return None
        from playwright.sync_api import sync_playwright

        limit = (timeout or self.timeout) * 1000.0
        with sync_playwright() as play:
            browser = play.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page()
                page.goto(url, timeout=limit, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()


RENDERERS = {"none": NullRenderer, "playwright": PlaywrightRenderer}


class RenderError(RuntimeError):
    """A page needed rendering and no renderer could do it."""


def build(cfg: Optional[dict] = None) -> Renderer:
    """The configured renderer, or the null one with the reason in its name."""
    block = ((cfg or {}).get("harvest", {}) or {}).get("render", {}) or {}
    name = str(block.get("backend") or "none").strip().lower()
    factory = RENDERERS.get(name)
    if factory is None:
        raise RenderError(f"unknown renderer {name!r}; available: "
                          f"{', '.join(sorted(RENDERERS))}")
    renderer = factory()
    return renderer if renderer.available() else NullRenderer()


def maybe_render(renderer: Renderer, url: str, html: str, *,
                 timeout: float = 20.0) -> tuple:
    """Render only when the page looks like a shell and a renderer exists.

    Returns `(html, rendered, reason)`. The reason is always a sentence, because a
    page that came back empty should say whether anything tried.
    """
    if not looks_like_a_shell(html):
        return html, False, "content is present; no rendering needed"
    if not renderer.available():
        return html, False, (f"page looks like a shell ({len(visible_text(html))} "
                             f"characters of text) and no renderer is configured")
    try:
        rendered = renderer.render(url, timeout=timeout)
    except Exception as exc:             # noqa: BLE001 - a render failure is data
        return html, False, f"render failed: {type(exc).__name__}: {exc}"
    if not rendered:
        return html, False, "the renderer returned nothing"
    return rendered, True, f"rendered with {renderer.name}"
