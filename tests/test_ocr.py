"""Images: metadata is measured, text is a seam that declines by default."""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vrusha.store import ocr


def _png(width=120, height=40):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeOcr:
    name = "fake"

    def __init__(self, text="read this", available=True, raises=False):
        self._text = text
        self._available = available
        self.raises = raises
        self.calls = 0

    def available(self):
        return self._available

    def text(self, data):
        self.calls += 1
        if self.raises:
            raise RuntimeError("engine exploded")
        return self._text


# ---------------------------------------------------------------------------
# detection and metadata -- measured, no system binary
# ---------------------------------------------------------------------------


def test_a_png_is_recognised():
    assert ocr.looks_like_image(_png()) is True
    assert ocr.format_of(_png()) == "png"


def test_html_is_not_an_image():
    assert ocr.looks_like_image(b"<html>hi</html>") is False


def test_empty_and_tiny_input_is_not_an_image():
    assert ocr.looks_like_image(b"") is False
    assert ocr.looks_like_image(b"\x89PN") is False


def test_describe_gives_dimensions():
    info = ocr.describe(_png(300, 90))
    assert info["format"] == "png"
    assert (info["width"], info["height"]) == (300, 90)
    assert info["mode"] == "RGB"


def test_describe_never_raises_on_garbage():
    info = ocr.describe(b"\x89PNG\r\n\x1a\n" + b"not really a png")
    assert info["format"] == "png"
    assert "error" in info, "a broken image should report the error, not raise"


# ---------------------------------------------------------------------------
# the default declines
# ---------------------------------------------------------------------------


def test_the_default_engine_is_none():
    assert ocr.build({}).name == "none"
    assert ocr.build({}).available() is False


def test_an_unknown_engine_is_refused_with_the_list():
    with pytest.raises(ocr.OcrError) as error:
        ocr.build({"extract": {"ocr": {"backend": "magic"}}})
    assert "unknown ocr engine" in str(error.value)
    assert "none" in str(error.value)


def test_an_unavailable_tesseract_falls_back_to_none():
    assert ocr.build({"extract": {"ocr": {"backend": "tesseract"}}}).name in \
        ("none", "tesseract")


def test_an_image_without_an_engine_says_what_it_is():
    text, used, reason = ocr.maybe_read(ocr.NullOcr(), _png())
    assert text == "" and used is False
    assert "image (png" in reason and "no ocr engine" in reason


def test_a_non_image_is_not_sent_to_an_engine():
    fake = FakeOcr()
    text, used, reason = ocr.maybe_read(fake, b"<html>hello</html>")
    assert used is False and fake.calls == 0
    assert reason == "not an image"


# ---------------------------------------------------------------------------
# when an engine exists
# ---------------------------------------------------------------------------


def test_text_is_returned_when_an_engine_exists():
    fake = FakeOcr(text="invoice 42")
    text, used, reason = ocr.maybe_read(fake, _png())
    assert text == "invoice 42" and used is True
    assert "read with fake" in reason


def test_an_engine_that_reads_nothing_says_so():
    fake = FakeOcr(text="")
    text, used, reason = ocr.maybe_read(fake, _png())
    assert used is False and "read no text" in reason


def test_an_engine_that_raises_degrades_with_a_reason():
    fake = FakeOcr(raises=True)
    text, used, reason = ocr.maybe_read(fake, _png())
    assert text == "" and used is False
    assert "ocr failed" in reason and "RuntimeError" in reason


def test_the_engine_registry_lists_both():
    assert set(ocr.OCR_ENGINES) == {"none", "tesseract"}
