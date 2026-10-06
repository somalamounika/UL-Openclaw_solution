"""Shared OpenAI / Azure OpenAI client helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_logged_models: set[str] = set()
_JSON_RETRY_ATTEMPTS = 2
_JSON_RETRY_DELAY_SEC = 0.6


def get_chat_client(*, model_override: str | None = None) -> tuple[Any, str]:
    """
    Create a chat completions client and resolve the model/deployment name.

    Returns (client, model_name).
    """
    use_azure = bool(config.AZURE_OPENAI_API_KEY and config.AZURE_OPENAI_ENDPOINT)

    if use_azure:
        from openai import AzureOpenAI

        model = model_override or config.AZURE_OPENAI_CHAT_DEPLOYMENT
        if not model:
            raise ValueError("AZURE_OPENAI_CHAT_DEPLOYMENT is not configured")
        client = AzureOpenAI(
            api_key=config.AZURE_OPENAI_API_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
        )
        if model not in _logged_models:
            logger.info("Using Azure OpenAI chat (deployment=%s)", model)
            _logged_models.add(model)
        return client, model

    if not config.LLM_API_KEY:
        raise ValueError("LLM_API_KEY is not configured")

    model = model_override or config.LLM_MODEL
    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    if model not in _logged_models:
        logger.info("Using OpenAI-compatible chat (model=%s)", model)
        _logged_models.add(model)
    return client, model


def _is_retryable_chat_error(exc: BaseException) -> bool:
    """True for Azure gpt-5 'invalid content' 500s and other transient chat failures."""
    status = getattr(exc, "status_code", None)
    if status in {429, 500, 502, 503}:
        return True
    text = str(exc).lower()
    return (
        "invalid content" in text
        or "model_error" in text
        or "empty response" in text
    )


def chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0,
    model: str | None = None,
) -> str:
    """Run a chat completion that must return a JSON object. Returns raw content.

    gpt-5* deployments often fail with HTTP 500 / model_error when
    ``response_format=json_object`` is forced. Those calls omit JSON mode and
    retry once; prompts already require JSON.
    """
    client, resolved = get_chat_client(model_override=model)
    is_gpt5 = resolved.lower().startswith("gpt-5")
    last_error: BaseException | None = None

    for attempt in range(_JSON_RETRY_ATTEMPTS):
        # gpt-5: never use JSON mode. Other models: drop it on retry.
        use_json_mode = not is_gpt5 and attempt == 0
        kwargs: dict[str, Any] = {
            "model": resolved,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if not is_gpt5:
            kwargs["temperature"] = temperature

        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            if not content:
                raise ValueError("LLM returned empty response")
            return content
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= _JSON_RETRY_ATTEMPTS or not _is_retryable_chat_error(exc):
                break
            logger.warning(
                "chat_json retry %d/%d for %s: %s",
                attempt + 1,
                _JSON_RETRY_ATTEMPTS,
                resolved,
                exc,
            )
            time.sleep(_JSON_RETRY_DELAY_SEC)

    raise last_error or ValueError("LLM returned empty response")
