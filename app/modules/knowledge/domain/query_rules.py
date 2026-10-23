"""Rules for turning a conversation turn into a knowledge-base search query."""


def enrich_query(message: str, history: list[dict] | None) -> str:
    """Manual fallback: prepend recent conversation context to the search query
    so the vector embedding carries brand / series / topic continuity."""
    if not history:
        return message
    parts: list[str] = []
    for turn in history[-3:]:
        content = (turn.get("content") or "").strip()
        if content:
            parts.append(content[:200])
    if parts:
        return " ".join(parts) + " " + message
    return message


def rewrite_prompt(message: str, history: list[dict] | None) -> str | None:
    """LLM rewrite prompt for a self-contained search query; None when the
    history has no usable user/assistant turns (caller answers unchanged)."""
    # Build a compact history summary (last 4 turns, 200 chars each)
    lines: list[str] = []
    for turn in (history or [])[-4:]:
        role = turn.get("role", "")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}: {content[:200]}")
    if not lines:
        return None

    prompt = (
        "你是搜索查询改写助手。根据对话历史和用户最新消息，"
        "生成一个自包含的搜索查询（中文），包含品牌、型号、主题等关键信息。"
        "只输出查询文本，不要任何解释。\n\n"
        f"对话历史:\n{chr(10).join(lines)}\n\n"
        f"用户最新消息: {message}\n\n"
        "改写后的查询:"
    )
    return prompt


def match_brand(rows: list[tuple[str, str | None]], query: str) -> str | None:
    """Keyword-match a single registered brand (value or Chinese label) into
    the query text. Zero or 2+ matches -> None (ambiguous — don't filter).

    Deliberately not an LLM call: this runs on every request (unlike
    ``rewrite_query()``, which skips the LLM when there's no history), so a
    cheap keyword match against the ~17 registered brands (resource_tags,
    type=doc_brand) keeps latency/cost at zero. See prod/rag-optimization-design.md
    §4 Step 4 for the tradeoff discussion.
    """
    q_lower = query.lower()
    matched: set[str] = set()
    for value, label_zh in rows:
        if value and value.lower() in q_lower:
            matched.add(value)
        elif label_zh and label_zh in query:
            matched.add(value)
    return matched.pop() if len(matched) == 1 else None

