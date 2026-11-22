"""The text stored and returned for an error: the class and the message, without URLs or credentials, cut to a fixed length."""

import re

MAX_ERROR_LENGTH = 1000


def describe_error(error: BaseException) -> str:
    """`ClassName: message`, with any URL (which may carry a password) replaced by `<url>`, at most 1000 characters."""
    text = f"{type(error).__name__}: {error}".strip()
    cleaned = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", text, flags=re.I)
    return cleaned[:MAX_ERROR_LENGTH]
