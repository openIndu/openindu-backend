"""LLM-backed query rewriting with a manual enrichment fallback."""
from typing import Protocol

from app.modules.knowledge.domain.query_rules import enrich_query, rewrite_prompt


class LlmGateway(Protocol):
    async def complete(self, *, prompt: str, model: str, temperature: float, max_tokens: int) -> str | None: ...


class RewriteQuery:
    def __init__(self, llm: LlmGateway, *, model: str, api_key: str | None) -> None:
        self.llm = llm
        self.model = model
        self.api_key = api_key

    async def execute(self, message: str, history: list[dict] | None) -> str:
        if not history or not self.api_key:
            return enrich_query(message, history)
        prompt = rewrite_prompt(message, history)
        if prompt is None:
            return message
        try:
            rewritten = await self.llm.complete(prompt=prompt, model=self.model, temperature=0, max_tokens=100)
            if rewritten and rewritten.strip():
                return rewritten.strip()
        except Exception:
            pass  # LLM unavailable → fall through to manual enrichment
        return enrich_query(message, history)
