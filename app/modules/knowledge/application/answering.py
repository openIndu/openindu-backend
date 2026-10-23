"""Stream a grounded/fallback consultation answer as SSE frames."""
import json
from typing import Any, Protocol

from app.modules.knowledge.domain.answering import build_messages, determine_mode


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class LlmStreamGateway(Protocol):
    async def stream(self, *, messages: list[dict], model: str, temperature: float, max_tokens: int) -> Any: ...


class UsageLedger(Protocol):
    def backfill_usage(self, log_id: int | None, usage: dict) -> None: ...


class StreamAnswer:
    def __init__(self, llm: LlmStreamGateway, usage_ledger: UsageLedger, *, model: str, api_key: str | None, temperature: float, max_tokens: int) -> None:
        self.llm = llm
        self.usage_ledger = usage_ledger
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def execute(self, message, history, chunks, sources, log_id):
        """Async generator yielding SSE frames: sources -> mode -> delta* -> done (or error).

        根据 determine_mode 决定是 grounded 还是 fallback：
        - grounded: sources 内含命中文档；LLM 严格依据片段作答。
        - fallback: sources 为空；前端据 mode 事件展示『非知识库』警告条。

        On completion it backfills the chat_logs row (log_id) with token usage using
        a fresh DB session — the request-scoped session is already closed by the time
        a StreamingResponse body runs.
        """
        mode = determine_mode(chunks)
        # 1) sources：fallback 模式下用空数组（保持事件协议向后兼容）
        yield _sse("sources", sources if mode == "grounded" else [])
        # 2) mode：明示当前回答来源，前端据此切换 UI 样式
        yield _sse("mode", {"mode": mode})

        if not self.api_key:
            yield _sse("error", {"detail": "智能咨询未配置大模型（缺少 LLM_API_KEY）"})
            return

        usage: dict = {}
        try:
            stream = await self.llm.stream(
                model=self.model,
                messages=build_messages(message, history, chunks, mode=mode),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield _sse("delta", {"text": delta.content})
        except Exception as exc:  # noqa: BLE001 - surface a clean SSE error to the client
            yield _sse("error", {"detail": f"生成失败：{exc}"})
            return

        self.usage_ledger.backfill_usage(log_id, usage)
        yield _sse("done", {"finish_reason": "stop", "usage": usage, "mode": mode})
