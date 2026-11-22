"""Readability metrics of the converted Markdown, to anticipate what an RFP costs to process (not a literary grade).

`py-readability-metrics` computes Flesch-Kincaid, Gunning Fog, Flesch reading ease and ARI. The library refuses texts under 100 words and needs NLTK's
`punkt_tab` data; when it cannot score, the metric is `None` and `note` says why: a missing number is honest, an invented one is not.
The size of the document (words, estimated tokens) is always there, and it is what decides the cost band.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("trackflow.rfp_intake")

CHARS_PER_TOKEN = 4  # the usual rule of thumb for English and Spanish prose
BANDS = ((1_500, "low"), (6_000, "medium"))  # estimated tokens: below 1.5k low, below 6k medium, otherwise high
_METRICS = {"flesch_kincaid_grade": "flesch_kincaid", "gunning_fog": "gunning_fog", "flesch_reading_ease": "flesch", "ari": "ari"}


def plain_text(markdown: str) -> str:
    """The prose the library should score: Markdown marks and links removed, every line ending like a sentence."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"^[#>\-*\s|]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`|]", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(line if re.search(r"[.!?:]$", line) else line + "." for line in lines)


def _library_scores(text: str) -> tuple[dict, str]:
    try:
        from readability import Readability
        from readability.exceptions import ReadabilityException
    except ImportError:
        return {}, "py-readability-metrics is not installed"
    try:
        reader = Readability(text)
    except ReadabilityException as error:
        return {}, str(error)
    except LookupError:
        try:  # first run: the sentence tokenizer data of NLTK is downloaded once
            import nltk

            nltk.download("punkt_tab", quiet=True)
            reader = Readability(text)
        except Exception as error:  # noqa: BLE001
            return {}, f"NLTK data 'punkt_tab' is unavailable ({type(error).__name__})"
    scores, notes = {}, []
    for key, method in _METRICS.items():
        try:
            scores[key] = round(float(getattr(reader, method)().score), 2)
        except ReadabilityException as error:
            notes.append(str(error))
        except Exception as error:  # noqa: BLE001 - one metric that cannot be computed must not stop the ticket
            logger.warning("readability metric %s failed: %s", key, type(error).__name__)
    return scores, "; ".join(dict.fromkeys(notes))


def analyze(markdown: str) -> dict:
    """The metrics stored with the ticket."""
    prose = plain_text(markdown)
    tokens = max(1, round(len(markdown) / CHARS_PER_TOKEN))
    scores, note = _library_scores(prose)
    return {
        "word_count": len(re.findall(r"\w+(?:['’-]\w+)*", prose)),
        "character_count": len(markdown),
        "estimated_tokens": tokens,
        "cost_band": next((label for limit, label in BANDS if tokens < limit), "high"),
        "flesch_kincaid_grade": scores.get("flesch_kincaid_grade"),
        "gunning_fog": scores.get("gunning_fog"),
        "flesch_reading_ease": scores.get("flesch_reading_ease"),
        "ari": scores.get("ari"),
        "library": "py-readability-metrics",
        "note": note,
    }
