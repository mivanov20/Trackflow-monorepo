"""One small HTTP client for the model service (OpenAI-compatible API: `/embeddings` and `/chat/completions`).

No orchestration framework: a POST with a bearer token. The service address and key come from `LLM_API_URL` and `LLM_API_KEY`
(the third-party models, such as DeepSeek or a GPT model, that the academy pays for and exposes to its students). The key and the text sent are never written to a log or an error message.
"""

from __future__ import annotations

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
