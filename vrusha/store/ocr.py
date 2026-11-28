"""Images: what they are (measured), and what they say (a seam).

The outside review rated document intelligence 3/10, and images are half of why:
`harvest` reads HTML, `pdf.py` reads positioned text, and an image was bytes that
went nowhere.

Two things are separable here, and only one of them needs a system binary:

**What an image is** — format, dimensions, mode — needs Pillow, which is an
**optional extra** (`pip install vrusha[images]`), not a core dependency. Without it
`describe()` still returns the format and byte count and records the missing
dependency in an `error` key, so a machine without Pillow degrades instead of
failing.

**What an image says** needs Tesseract, a system binary. It is a seam, like the
browser in Part 36: `NullOcr` is the default and declines, `TesseractOcr` is
optional and lazy, and its absence is a sentence rather than a traceback.

**Honest limit:** Tesseract is not installed on this machine and cannot be (no root),
so its accuracy and speed are **unmeasured**. The seam is tested with a fake engine;
the real one is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Protocol

#: Magic bytes for the formats a page actually serves.
MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"BM": "bmp",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
}


def looks_like_image(data: bytes) -> bool:
    """Magic bytes, not a file extension: an extension is a claim, not a fact."""
    if not data or len(data) < 8:
        return False
    for magic in MAGIC:
        if data.startswith(magic):
            return True
    # WEBP is `RIFF....WEBP`
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def format_of(data: bytes) -> str:
    for magic, name in MAGIC.items():
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def describe(data: bytes) -> Dict[str, object]:
    """Format and dimensions. Measured, needs no system binary, never raises."""
    info: Dict[str, object] = {"bytes": len(data or b""), "format": format_of(data)}
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            info["width"], info["height"] = image.size
            info["mode"] = image.mode
    except Exception as exc:                 # noqa: BLE001 - a bad image is data
        info["error"] = f"{type(exc).__name__}"
    return info


class Ocr(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def text(self, data: bytes) -> str:
        ...


@dataclass
class NullOcr:
    """The default. Declines, and says why."""

    name: str = "none"

    def available(self) -> bool:
        return False

    def text(self, data: bytes) -> str:
        return ""


@dataclass
class TesseractOcr:
    """Tesseract through pytesseract. Optional, lazy, unmeasured here."""

    name: str = "tesseract"
    language: str = "eng"

    def available(self) -> bool:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            return True
        except Exception:                    # noqa: BLE001 - optional dependency
            return False

    def text(self, data: bytes) -> str:
        if not self.available():
            return ""
        from io import BytesIO

        import pytesseract
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            return pytesseract.image_to_string(image, lang=self.language).strip()


OCR_ENGINES = {"none": NullOcr, "tesseract": TesseractOcr}


class OcrError(RuntimeError):
    """An image needed reading and no engine could do it."""


def build(cfg: Optional[dict] = None) -> Ocr:
    block = ((cfg or {}).get("extract", {}) or {}).get("ocr", {}) or {}
    name = str(block.get("backend") or "none").strip().lower()
    factory = OCR_ENGINES.get(name)
    if factory is None:
        raise OcrError(f"unknown ocr engine {name!r}; available: "
                       f"{', '.join(sorted(OCR_ENGINES))}")
    engine = factory()
    return engine if engine.available() else NullOcr()


def maybe_read(engine: Ocr, data: bytes) -> tuple:
    """Text if an engine exists, plus a sentence saying what happened."""
    if not looks_like_image(data):
        return "", False, "not an image"
    if not engine.available():
        described = describe(data)
        return "", False, (f"image ({described.get('format')}, "
                           f"{described.get('bytes')} bytes) and no ocr engine is "
                           f"configured")
    try:
        text = engine.text(data) or ""
    except Exception as exc:                 # noqa: BLE001 - a failure is data
        return "", False, f"ocr failed: {type(exc).__name__}: {exc}"
    if not text:
        return "", False, f"{engine.name} read no text"
    return text, True, f"read with {engine.name}"
