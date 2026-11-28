"""The render seam: detect a shell for free, and refuse to pay for a browser."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.harvest import render

SHELL = ('<html><head><title>App</title></head><body><div id="root"></div>'
         + '<script src="/app.js"></script>' * 30 + "</body></html>")
REAL = ("<html><body><p>"
        + "the licence gate refuses a page with no licence recorded. " * 20
        + "</p></body></html>")


class FakeRenderer:
    name = "fake"

    def __init__(self, html=None, available=True, raises=False):
        self.html = html
        self._available = available
        self.raises = raises
        self.calls = 0

    def available(self):
        return self._available

    def render(self, url, *, timeout=20.0):
        self.calls += 1
        if self.raises:
            raise RuntimeError("browser crashed")
        return self.html


# ---------------------------------------------------------------------------
# detection is free
# ---------------------------------------------------------------------------


def test_a_shell_is_detected():
    assert render.looks_like_a_shell(SHELL) is True


def test_a_real_page_is_not_a_shell():
    assert render.looks_like_a_shell(REAL) is False


def test_a_tiny_page_is_not_judged():
    """Too small to measure: `looks_like_a_shell` declines rather than guessing."""
    assert render.looks_like_a_shell("<html></html>") is False


def test_visible_text_drops_scripts_and_style():
    html = "<style>.a{}</style><p>hello</p><script>var x=1</script>"
    assert render.visible_text(html) == "hello"


# ---------------------------------------------------------------------------
# the default refuses to pay
# ---------------------------------------------------------------------------


def test_the_default_backend_is_none():
    assert render.build({}).name == "none"
    assert render.build({}).available() is False


def test_an_unknown_backend_is_refused_with_the_list():
    with pytest.raises(render.RenderError) as error:
        render.build({"harvest": {"render": {"backend": "magic"}}})
    assert "unknown renderer" in str(error.value)
    assert "none" in str(error.value)


def test_an_unavailable_playwright_falls_back_to_none():
    """No browser installed means the old behaviour, not a crash."""
    assert render.build({"harvest": {"render": {"backend": "playwright"}}}).name \
        in ("none", "playwright")


def test_a_shell_without_a_renderer_says_so():
    html, rendered, reason = render.maybe_render(render.NullRenderer(), "u", SHELL)
    assert html == SHELL and rendered is False
    assert "shell" in reason and "no renderer" in reason


def test_a_real_page_is_not_rendered():
    fake = FakeRenderer(html="<html>rendered</html>")
    html, rendered, reason = render.maybe_render(fake, "u", REAL)
    assert rendered is False
    assert fake.calls == 0, "a browser was started for a page that did not need one"
    assert "no rendering needed" in reason


# ---------------------------------------------------------------------------
# when a renderer is there
# ---------------------------------------------------------------------------


def test_a_shell_is_rendered_when_a_renderer_exists():
    fake = FakeRenderer(html="<html><p>real content now</p></html>")
    html, rendered, reason = render.maybe_render(fake, "u", SHELL)
    assert rendered is True
    assert "real content now" in html
    assert "rendered with fake" in reason


def test_a_renderer_that_returns_nothing_leaves_the_page_alone():
    fake = FakeRenderer(html=None)
    html, rendered, reason = render.maybe_render(fake, "u", SHELL)
    assert html == SHELL and rendered is False
    assert "returned nothing" in reason


def test_a_renderer_that_raises_degrades_with_a_reason():
    fake = FakeRenderer(raises=True)
    html, rendered, reason = render.maybe_render(fake, "u", SHELL)
    assert html == SHELL and rendered is False
    assert "render failed" in reason and "RuntimeError" in reason


def test_the_renderer_registry_lists_both():
    assert set(render.RENDERERS) == {"none", "playwright"}
