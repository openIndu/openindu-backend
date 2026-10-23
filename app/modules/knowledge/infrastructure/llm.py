"""OpenAI-compatible LLM gateway (DeepSeek by default), lazily connected."""
from typing import Any

from app.shared.core.config import settings

_LLM_CLIENT = None


def _get_client():
    """Lazily build the async OpenAI-compatible client (DeepSeek by default)."""
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        from openai import AsyncOpenAI

        _LLM_CLIENT = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    return _LLM_CLIENT


class OpenAiCompatibleLlm:
    """Application gateway port impl: single completions + streamed completions."""

    async def complete(self, *, prompt: str, model: str, temperature: float, max_tokens: int) -> str | None:
        client = _get_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    async def stream(self, *, messages: list[dict], model: str, temperature: float, max_tokens: int) -> Any:
        client = _get_client()
        return await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
