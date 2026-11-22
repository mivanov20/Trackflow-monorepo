"""One small HTTP client for the model service (OpenAI-compatible API: `/embeddings` and `/chat/completions`).

No orchestration framework: a POST with a bearer token. The service address and key come from `LLM_API_URL` and `LLM_API_KEY`
(the third-party models, such as DeepSeek or a GPT model, that the academy pays for and exposes to its students). The key and the text sent are never written to a log or an error message.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx

from data.process import rag_config as config


class ModelServiceError(RuntimeError):
    """The model service is not configured, not reachable, or answered with something unusable."""


def post(path: str, payload: dict) -> dict:
    url, key = config.llm_api_url(), config.llm_api_key()
    if not url or not key:
        raise ModelServiceError("The model service is not configured: set LLM_API_URL and LLM_API_KEY.")
    try:
        response = httpx.post(f"{url}{path}", json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=config.REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        raise ModelServiceError(f"The model service answered {error.response.status_code} for {path}.") from None
    except (httpx.HTTPError, ValueError) as error:
        raise ModelServiceError(f"The model service could not be reached for {path} ({type(error).__name__}).") from None


def stream_chat(payload: dict, on_open: Callable[[Callable[[], None]], None] | None = None) -> Iterator[str]:
    """The text of a chat completion as it is generated: the same `/chat/completions` call with `stream: true`, one piece of text per server-sent chunk.

    `on_open` receives a function that closes the connection to the model. It can be called from ANOTHER thread to abort a generation that is waiting for its next token: the model service
    stops generating when its client goes away. Leaving the iterator early (or closing it) also closes the connection. Errors are the same `ModelServiceError` as `post`.
    """
    url, key = config.llm_api_url(), config.llm_api_key()
    if not url or not key:
        raise ModelServiceError("The model service is not configured: set LLM_API_URL and LLM_API_KEY.")
    try:
        with httpx.stream("POST", f"{url}/chat/completions", json={**payload, "stream": True}, headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}, timeout=config.REQUEST_TIMEOUT_SECONDS) as response:
            if on_open is not None:
                on_open(response.close)
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if isinstance(delta, str) and delta:
                    yield delta
    except httpx.HTTPStatusError as error:
        raise ModelServiceError(f"The model service answered {error.response.status_code} for /chat/completions.") from None
    except (httpx.HTTPError, ValueError) as error:
        raise ModelServiceError(f"The model service could not be reached for /chat/completions ({type(error).__name__}).") from None
