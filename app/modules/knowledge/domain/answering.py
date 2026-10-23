"""Answer construction for RAG consultations: modes, prompts and citations."""

# 最多带入的历史轮数（user/assistant 合计），防止 prompt 过长
MAX_HISTORY = 6

# 严格 grounded 模式：只能依据知识库片段回答（默认模式，工业参数零容错）。
GROUNDED_SYSTEM_PROMPT = (
    "你是 openIndu 工业自动化知识库助手。"
    "只能依据下面提供的【知识库片段】回答用户问题；"
    "若片段不足以回答，明确告知『未在知识库中找到相关资料，建议到下载中心查阅原始手册或换个问法』，"
    "严禁编造型号参数、接线、寄存器地址、版本号等任何未在片段中出现的内容。"
    "用中文作答，涉及参数或操作步骤时注明出自哪篇文档。"
)

# Fallback 模式：知识库无命中时启用，允许 LLM 用通用工业知识回答；
# 前端会单独显示『非来自本平台知识库』警告条，因此 system prompt 不需要重复警告——
# 但仍然严禁编造具体型号的参数细节（用户应以厂家原版手册为准）。
FALLBACK_SYSTEM_PROMPT = (
    "你是 openIndu 工业自动化助手。"
    "当前用户的问题在本平台知识库中没有相关资料，请基于你掌握的通用工业自动化知识作答。"
    "对于具体型号的寄存器地址、接线图、电气参数、版本号等关键细节，"
    "如不能 100% 确定，必须明示『请以厂家原版手册为准』，不得编造数字。"
    "回答开头无需提示来源缺失（前端会自行标注），直接进入正文。用中文作答。"
)

# Grounded → Fallback 的切换门槛（基于 COSINE 相似度，[0,1]）。
# 经验值：强相关问题 top-1 score ≥ 0.7；< 0.7 多为弱相关（同领域不同代号/异品牌词面命中等）
# —— 这类情况下让 LLM 用通用知识回答比硬咬不匹配的片段体验更好。
# 业务案例：
#   富士以太网通信 (top=0.74) → grounded ✅
#   富士 PLC 编程示例 (top=0.67) → fallback（命中触摸屏/异品牌运控，不算真相关）
#   FX5U 地址规划 (top=0.54) → fallback ✅
GROUNDED_SCORE_THRESHOLD = 0.7


def determine_mode(chunks: list[dict]) -> str:
    """判定回答模式：grounded（依据知识库）/ fallback（依据 LLM 通用知识）。

    切换条件：chunks 为空，或最高 score 低于阈值——说明检索结果与问题弱相关。
    """
    if not chunks:
        return "fallback"
    top_score = max((c.get("score", 0) for c in chunks), default=0)
    if top_score < GROUNDED_SCORE_THRESHOLD:
        return "fallback"
    return "grounded"


def sources_from(chunks: list[dict]) -> list[dict]:
    """Dedupe retrieved chunks into citation entries (by document_name + page)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for c in chunks:
        meta = c.get("metadata", {})
        key = (meta.get("document_name", ""), meta.get("page", 0))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "document_name": meta.get("document_name", ""),
            "page": meta.get("page", 0),
            "brand": meta.get("brand", ""),
            "category": meta.get("category", ""),
            "score": c.get("score", 0),
        })
    return out


def build_messages(
    message: str,
    history: list[dict] | None,
    chunks: list[dict],
    mode: str = "grounded",
) -> list[dict]:
    """Assemble chat messages: system + (trimmed) history + user turn.

    - mode="grounded": 严格基于检索片段作答，user turn 内嵌【知识库片段】。
    - mode="fallback": 通用工业知识作答，user turn 不带知识库上下文。

    无论哪种模式，用户问题都嵌在 user role 的内容里、不能覆盖 system 指令——
    这是基本的 prompt-injection 防护。
    """
    if mode == "fallback":
        messages: list[dict] = [{"role": "system", "content": FALLBACK_SYSTEM_PROMPT}]
        for turn in (history or [])[-MAX_HISTORY:]:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})
        return messages

    # grounded
    context_lines = []
    for i, c in enumerate(chunks, 1):
        meta = c.get("metadata", {})
        name = meta.get("document_name", "未知文档")
        page = meta.get("page", 0)
        context_lines.append(f"[来源{i}]《{name}》p.{page}\n{c.get('text', '')}")
    context = "\n\n".join(context_lines) if context_lines else "（无相关片段）"

    messages = [{"role": "system", "content": GROUNDED_SYSTEM_PROMPT}]
    for turn in (history or [])[-MAX_HISTORY:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({
        "role": "user",
        "content": f"【知识库片段】\n{context}\n\n【用户问题】\n{message}",
    })
    return messages
