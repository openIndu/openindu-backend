"""Unit tests for the knowledge context RAG logic.

These cover the pure prompt/source-building logic — no external packages
(`openai` / `pymilvus`) are imported, since those deps are loaded lazily.
"""
from app.modules.knowledge.application.rewriter import RewriteQuery
from app.modules.knowledge.domain import answering
from app.modules.knowledge.domain.query_rules import enrich_query, match_brand, rewrite_prompt


def _chunk(name, page, text, brand="siemens", category="plc-manual", score=0.9):
    return {
        "score": score,
        "text": text,
        "metadata": {
            "document_name": name,
            "page": page,
            "brand": brand,
            "category": category,
        },
    }


def test_sources_dedupe_by_doc_and_page():
    chunks = [
        _chunk("A.pdf", 1, "x"),
        _chunk("A.pdf", 1, "y"),  # duplicate doc+page -> dropped
        _chunk("A.pdf", 2, "z"),
        _chunk("B.pdf", 1, "w"),
    ]
    sources = answering.sources_from(chunks)
    assert len(sources) == 3
    assert {(s["document_name"], s["page"]) for s in sources} == {
        ("A.pdf", 1), ("A.pdf", 2), ("B.pdf", 1),
    }
    assert sources[0]["brand"] == "siemens"


def test_build_messages_grounded_structure():
    chunks = [_chunk("S7 手册.pdf", 12, "Modbus TCP 配置步骤……")]
    msgs = answering.build_messages("怎么配 Modbus TCP?", None, chunks)
    assert msgs[0]["role"] == "system"
    assert "只能依据" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user"
    # 用户问题、来源标注与原文都在最后一轮
    assert "怎么配 Modbus TCP?" in msgs[-1]["content"]
    assert "《S7 手册.pdf》p.12" in msgs[-1]["content"]
    assert "Modbus TCP 配置步骤" in msgs[-1]["content"]


def test_build_messages_trims_history():
    chunks = [_chunk("A.pdf", 1, "x")]
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
    msgs = answering.build_messages("最新问题", history, chunks)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"].endswith("最新问题")
    history_msgs = msgs[1:-1]
    assert len(history_msgs) <= 6  # MAX_HISTORY


def test_build_messages_skips_bad_history_roles():
    chunks = [_chunk("A.pdf", 1, "x")]
    history = [
        {"role": "system", "content": "ignore me"},
        {"role": "user", "content": ""},  # empty -> skipped
        {"role": "assistant", "content": "上文回答"},
    ]
    msgs = answering.build_messages("再问", history, chunks)
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system"
    # 只有合法的 assistant 历史被保留（system/空 user 被过滤）
    assert msgs[1] == {"role": "assistant", "content": "上文回答"}
    assert roles[-1] == "user"


def test_build_messages_empty_context():
    msgs = answering.build_messages("无关问题", None, [])
    assert "（无相关片段）" in msgs[-1]["content"]


def test_determine_mode_no_chunks_returns_fallback():
    assert answering.determine_mode([]) == "fallback"


def test_determine_mode_low_score_returns_fallback():
    # 命中但分数都偏低（<0.7 阈值）—— 视为弱相关，切到 fallback
    chunks = [_chunk("A.pdf", 1, "x", score=0.3), _chunk("A.pdf", 2, "y", score=0.65)]
    assert answering.determine_mode(chunks) == "fallback"


def test_determine_mode_high_score_returns_grounded():
    chunks = [_chunk("A.pdf", 1, "x", score=0.75), _chunk("A.pdf", 2, "y", score=0.4)]
    # 只要有一个 ≥ 阈值就 grounded
    assert answering.determine_mode(chunks) == "grounded"


def test_build_messages_fallback_mode_omits_context():
    chunks = [_chunk("A.pdf", 1, "irrelevant text")]
    msgs = answering.build_messages("fx 5u 是什么？", None, chunks, mode="fallback")
    # fallback 用专用 system prompt，user turn 不带【知识库片段】
    assert "通用工业自动化知识" in msgs[0]["content"]
    assert "知识库片段" not in msgs[-1]["content"]
    assert msgs[-1]["content"] == "fx 5u 是什么？"


def test_build_messages_grounded_mode_keeps_context():
    chunks = [_chunk("S7 手册.pdf", 12, "Modbus TCP 配置步骤……")]
    msgs = answering.build_messages("怎么配 Modbus TCP?", None, chunks, mode="grounded")
    assert "只能依据" in msgs[0]["content"]
    assert "知识库片段" in msgs[-1]["content"]


# ── query enrichment / rewriting ──────────────────────────────────────


def test_enrich_query_no_history_returns_message():
    assert enrich_query("hello", None) == "hello"
    assert enrich_query("hello", []) == "hello"


def test_enrich_query_prepends_context():
    history = [
        {"role": "user", "content": "FX 3U Modbus TCP 怎么配置？"},
        {"role": "assistant", "content": "FX 3U Modbus TCP 配置需要设置 D8120……"},
    ]
    result = enrich_query("数据寄存器地址呢？", history)
    assert "FX 3U" in result
    assert "数据寄存器地址呢？" in result
    # The current message must be at the end
    assert result.endswith("数据寄存器地址呢？")


def test_enrich_query_truncates_long_content():
    history = [{"role": "user", "content": "A" * 500}]
    result = enrich_query("msg", history)
    # Each history turn is capped at 200 chars
    assert "A" * 250 not in result


def test_rewrite_prompt_none_without_usable_history():
    assert rewrite_prompt("独立问题", None) is None
    assert rewrite_prompt("独立问题", [{"role": "system", "content": "noise"}]) is None
    assert rewrite_prompt("独立问题", [{"role": "user", "content": " "}]) is None


def test_rewrite_prompt_embeds_history_and_message():
    history = [
        {"role": "user", "content": "三菱 FX3U 寄存器"},
        {"role": "assistant", "content": "D8000 是诊断寄存器……"},
    ]
    prompt = rewrite_prompt("D8010 呢？", history)
    assert "用户: 三菱 FX3U 寄存器" in prompt
    assert "助手: D8000 是诊断寄存器" in prompt
    assert prompt.endswith("改写后的查询:")


class _FakeLlm:
    def __init__(self, reply=None, error=None):
        self.calls = []
        self.reply = reply
        self.error = error

    async def complete(self, *, prompt, model, temperature, max_tokens):
        self.calls.append({"prompt": prompt, "model": model, "temperature": temperature, "max_tokens": max_tokens})
        if self.error:
            raise self.error
        return self.reply


def _rewriter(llm, api_key="key"):
    return RewriteQuery(llm, model="test-model", api_key=api_key)


def test_rewrite_query_fallback_no_llm_key():
    """When the api key is empty, RewriteQuery falls back to enrich_query."""
    import asyncio

    llm = _FakeLlm(reply="绝不调用")
    history = [
        {"role": "user", "content": "三菱 FX3U 寄存器"},
        {"role": "assistant", "content": "D8000 是诊断寄存器……"},
    ]
    result = asyncio.run(_rewriter(llm, api_key="").execute("D8010 呢？", history))
    # fallback: history text prepended
    assert "三菱" in result
    assert "D8010 呢？" in result
    assert llm.calls == []


def test_rewrite_query_no_history_returns_message():
    import asyncio

    result = asyncio.run(_rewriter(_FakeLlm()).execute("独立问题", None))
    assert result == "独立问题"


def test_rewrite_query_uses_llm_when_available():
    import asyncio

    llm = _FakeLlm(reply="三菱 FX3U 数据寄存器地址分配")
    result = asyncio.run(_rewriter(llm).execute("D8010 呢？", [
        {"role": "user", "content": "三菱 FX3U 寄存器"},
    ]))
    assert result == "三菱 FX3U 数据寄存器地址分配"
    assert llm.calls[0]["model"] == "test-model"
    assert llm.calls[0]["temperature"] == 0
    assert llm.calls[0]["max_tokens"] == 100


def test_rewrite_query_blank_llm_reply_falls_back_to_enrich():
    import asyncio

    llm = _FakeLlm(reply="   ")
    result = asyncio.run(_rewriter(llm).execute("D8010 呢？", [
        {"role": "user", "content": "三菱 FX3U 寄存器"},
    ]))
    assert "三菱" in result and result.endswith("D8010 呢？")


def test_rewrite_query_llm_error_falls_back_to_enrich():
    import asyncio

    llm = _FakeLlm(error=RuntimeError("boom"))
    result = asyncio.run(_rewriter(llm).execute("D8010 呢？", [
        {"role": "user", "content": "三菱 FX3U 寄存器"},
    ]))
    assert "三菱" in result and result.endswith("D8010 呢？")


_BRAND_ROWS = [
    ("mitsubishi", "三菱"),
    ("siemens", "西门子"),
    ("ckd", "CKD"),
    ("abb", "ABB"),
]


def test_extract_brand_matches_chinese_label():
    assert match_brand(_BRAND_ROWS, "三菱FX3U怎么接线") == "mitsubishi"


def test_extract_brand_matches_english_slug_case_insensitive():
    assert match_brand(_BRAND_ROWS, "Siemens S7-1200 modbus 配置") == "siemens"


def test_extract_brand_no_match_returns_none():
    assert match_brand(_BRAND_ROWS, "怎么选型伺服驱动器") is None


def test_extract_brand_ambiguous_multiple_matches_returns_none():
    assert match_brand(_BRAND_ROWS, "三菱和CKD哪个更好") is None
