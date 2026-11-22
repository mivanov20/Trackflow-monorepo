"""PDF -> Markdown, the first thing that happens to an RFP and before any agent reads it (tokens: a PDF is heavy, its Markdown is not).

MarkItDown does the conversion. A small cleanup follows because PDF bullets come out as `(cid:127)`: they become Markdown bullets.
"""

from __future__ import annotations

import re
from pathlib import Path

MIN_TEXT_CHARS = 40  # a PDF that yields less is a scan or an empty file: there is nothing to read


class ConversionError(RuntimeError):
    """The PDF could not be turned into text (corrupt, encrypted, or only images). The message is safe to show."""


def clean_markdown(text: str) -> str:
    text = re.sub(r"\(cid:\d+\)", "-", text)
    text = text.replace("\r\n", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pdf_to_markdown(path: str | Path) -> str:
    try:
        from markitdown import MarkItDown

        text = MarkItDown().convert(str(path)).text_content
    except Exception as error:  # noqa: BLE001 - MarkItDown raises many kinds; the cause stays in the chain for the log, the user gets one clear message
        raise ConversionError("The PDF could not be read. It may be damaged, password-protected or made only of images.") from error
    text = clean_markdown(text or "")
    if len(text) < MIN_TEXT_CHARS:
        raise ConversionError("The PDF has no readable text (it may be a scan). Upload a PDF with selectable text.")
    return text
