"""Behavior tests for knowledge-context use cases (no external packages)."""
import asyncio
import json

import pytest

from app.domain.knowledge import KnowledgeCode, KnowledgeError
from app.modules.knowledge.application.answering import StreamAnswer
from app.modules.knowledge.application.consultation import (
    EnsureChatQuota,
    RecordConsultation,
    RecordUserTurn,
    RetrieveChunks,
    persist_after_stream,
)
from app.modules.knowledge.application.retrieval import Retriever
from app.modules.knowledge.application.sessions import (
    DeleteChatSession,
    EnsureOwnSession,
    RenameChatSession,
    SetMessageFeedback,
)


def _chunk(name, page, score, brand="siemens", text="t"):
    return {"score": score, "text": text, "metadata": {"document_name": name, "page": page, "brand": brand, "category": "plc-manual"}}


# ── EnsureChatQuota ──────────────────────────────────────────────────


class FakeLedger:
    def __init__(self, used=0):
        self.used = used
        self.records = []
        self.usage_backfills = []

    def today_count(self, user_id):
        return self.used

    def record(self, *, user_id, ip_address, question, source_docs_json):
        self.records.append({"user_id": user_id, "ip": ip_address, "question": question, "docs": source_docs_json})
        return 100 + len(self.records)

    def backfill_usage(self, log_id, usage):
        self.usage_backfills.append((log_id, usage))


def test_quota_blocks_non_admin_at_limit():
    with pytest.raises(KnowledgeError) as exc:
        EnsureChatQuota(FakeLedger(used=5), 5).execute(role="member", user_id=1)
    assert exc.value.code == KnowledgeCode.QUOTA_EXCEEDED
    assert exc.value.params == {"limit": 5}


def test_quota_admin_exempt_and_under_limit_passes():
    quota = EnsureChatQuota(FakeLedger(used=99), 100)
    quota.execute(role="admin", user_id=1)  # admin: never blocked
    quota.execute(role="member", user_id=1)  # 99 < 100: ok


# ── RetrieveChunks ───────────────────────────────────────────────────


class FakeRetriever:
    def __init__(self, result=None, error=None):
        self.result = result or []
        self.error = error

    def retrieve(self, message, top_k=None, filters=None):
        if self.error:
            raise self.error
        return self.result


def test_retrieve_chunks_maps_failure_to_unavailable():
    with pytest.raises(KnowledgeError) as exc:
        RetrieveChunks(FakeRetriever(error=RuntimeError("milvus down"))).execute("q")
    assert exc.value.code == KnowledgeCode.RETRIEVAL_UNAVAILABLE
    assert isinstance(exc.value.__cause__, RuntimeError)


def test_retrieve_chunks_passthrough_result():
    chunks = [_chunk("A.pdf", 1, 0.9)]
    assert RetrieveChunks(FakeRetriever(chunks)).execute("q") == chunks


# ── Retriever brand auto-extract + retry ─────────────────────────────


class FakeSearch:
    def __init__(self, results_by_filter):
        self.results_by_filter = results_by_filter  # {repr(where_or_None): chunks}
        self.calls = []

    def search(self, query, top_k, where_filter):
        self.calls.append(where_filter)
        return self.results_by_filter.get(repr(where_filter), [])


BRANDS = [("mitsubishi", "三菱"), ("siemens", "西门子")]


def test_retriever_auto_brand_used_when_relevant():
    search = FakeSearch({repr({"category": "plc-manual", "brand": "mitsubishi"}): [_chunk("FX.pdf", 1, 0.9, "mitsubishi")]})
    retriever = Retriever(search, FakeBrandDirectory(BRANDS), default_top_k=5)
    chunks = retriever.retrieve("三菱FX3U怎么接线", filters={"brand": None, "category": "plc-manual"})
    assert chunks and search.calls[0] == {"brand": "mitsubishi", "category": "plc-manual"}


def test_retriever_auto_brand_retried_unfiltered_when_weak():
    search = FakeSearch({
        repr({"brand": "mitsubishi"}): [_chunk("HMI.pdf", 1, 0.4, "mitsubishi")],  # weak hit under threshold
        repr(None): [_chunk("FX.pdf", 1, 0.8, "mitsubishi")],  # unfiltered retry
    })
    retriever = Retriever(search, FakeBrandDirectory(BRANDS), default_top_k=5)
    chunks = retriever.retrieve("三菱FX3U怎么接线")
    assert chunks[0]["metadata"]["document_name"] == "FX.pdf"
    assert search.calls == [{"brand": "mitsubishi"}, None]  # empty where -> None


def test_retriever_explicit_brand_is_trusted_no_retry():
    search = FakeSearch({})  # empty result for the explicit filter: still no retry
    retriever = Retriever(search, FakeBrandDirectory(BRANDS), default_top_k=5)
    assert retriever.retrieve("随便", filters={"brand": "siemens"}) == []
    assert search.calls == [{"brand": "siemens"}]


class FakeBrandDirectory:
    def __init__(self, rows):
        self.rows = rows

    def registered_brands(self):
        return self.rows


# ── RecordConsultation ───────────────────────────────────────────────


def test_record_consultation_truncates_and_logs_doc_names():
    ledger = FakeLedger()
    chunks = [_chunk("A.pdf", 1, 0.9), _chunk("B.pdf", 2, 0.8)]
    sources, log_id = RecordConsultation(ledger).execute(
        user_id=7, ip_address="1.2.3.4", question="q" * 3000, chunks=chunks
    )
    assert log_id == 101
    rec = ledger.records[0]
    assert rec["question"] == "q" * 2000  # capped
    assert json.loads(rec["docs"]) == ["A.pdf", "B.pdf"]
    assert [s["document_name"] for s in sources] == ["A.pdf", "B.pdf"]


# ── RecordUserTurn ───────────────────────────────────────────────────


class FakeTranscripts:
    def __init__(self):
        self.turns = []
        self.replies = []

    def record_user_turn(self, session_id, content, auto_title):
        self.turns.append((session_id, content, auto_title))

    def save_assistant_reply(self, session_id, content, sources_json, mode):
        self.replies.append((session_id, content, sources_json, mode))


def test_record_user_turn_auto_titles_first_message_of_default_session():
    tr = FakeTranscripts()
    RecordUserTurn(tr).execute(session_id=1, session_title="新会话", had_history=False, question="  FX3U选型  ")
    assert tr.turns == [(1, "  FX3U选型  ", "FX3U选型")]  # stored raw; only the title is stripped


def test_record_user_turn_no_auto_title_with_history_or_custom_title():
    tr = FakeTranscripts()
    RecordUserTurn(tr).execute(session_id=1, session_title="新会话", had_history=True, question="q")
    RecordUserTurn(tr).execute(session_id=1, session_title="已命名", had_history=False, question="q")
    assert tr.turns == [(1, "q", None), (1, "q", None)]


def test_record_user_turn_caps_question_at_4000():
    tr = FakeTranscripts()
    RecordUserTurn(tr).execute(session_id=1, session_title="x", had_history=True, question="q" * 5000)
    assert tr.turns[0][1] == "q" * 4000


# ── session use cases ────────────────────────────────────────────────


class FakeSession:
    def __init__(self, sid, title="新会话"):
        self.id = sid
        self.title = title

    def to_dict(self):
        return {"id": self.id, "title": self.title}


class FakeSessions:
    def __init__(self, owned=None):
        self.owned = owned or {}
        self.renamed = []
        self.deleted = []
        self.feedback = []

    def find_owned(self, session_id, user_id):
        return self.owned.get((session_id, user_id))

    def create(self, user_id):
        return FakeSession(999)

    def rename(self, session, title):
        self.renamed.append((session.id, title))
        session.title = title
        return session

    def delete(self, session):
        self.deleted.append(session.id)

    def set_feedback(self, session_id, message_id, value):
        self.feedback.append((session_id, message_id, value))
        return message_id != 404


def test_ensure_own_session_raises_for_foreign_session():
    with pytest.raises(KnowledgeError) as exc:
        EnsureOwnSession(FakeSessions()).execute(1, 7)
    assert exc.value.code == KnowledgeCode.SESSION_NOT_FOUND


def test_rename_session_strips_caps_and_ownership():
    sessions = FakeSessions({(5, 7): FakeSession(5)})
    result = RenameChatSession(sessions).execute(session_id=5, user_id=7, title="  新标题  ")
    assert result.title == "新标题"
    assert sessions.renamed == [(5, "新标题")]


def test_rename_session_rejects_blank_title():
    with pytest.raises(KnowledgeError) as exc:
        RenameChatSession(FakeSessions({(5, 7): FakeSession(5)})).execute(session_id=5, user_id=7, title="   ")
    assert exc.value.code == KnowledgeCode.TITLE_EMPTY


def test_rename_session_rejects_foreign_session():
    with pytest.raises(KnowledgeError) as exc:
        RenameChatSession(FakeSessions()).execute(session_id=5, user_id=7, title="x")
    assert exc.value.code == KnowledgeCode.SESSION_NOT_FOUND


def test_delete_session_requires_ownership():
    sessions = FakeSessions({(5, 7): FakeSession(5)})
    DeleteChatSession(sessions).execute(session_id=5, user_id=7)
    assert sessions.deleted == [5]
    with pytest.raises(KnowledgeError):
        DeleteChatSession(sessions).execute(session_id=6, user_id=7)


def test_feedback_value_checked_before_ownership():
    sessions = FakeSessions()  # nothing owned
    with pytest.raises(KnowledgeError) as exc:
        SetMessageFeedback(sessions).execute(session_id=1, user_id=1, message_id=1, value=0)
    assert exc.value.code == KnowledgeCode.FEEDBACK_INVALID  # not SESSION_NOT_FOUND


def test_feedback_unknown_message_is_404():
    with pytest.raises(KnowledgeError) as exc:
        SetMessageFeedback(FakeSessions({(1, 7): FakeSession(1)})).execute(session_id=1, user_id=7, message_id=404, value=1)
    assert exc.value.code == KnowledgeCode.MESSAGE_NOT_FOUND


def test_feedback_ok():
    sessions = FakeSessions({(1, 7): FakeSession(1)})
    SetMessageFeedback(sessions).execute(session_id=1, user_id=7, message_id=9, value=-1)
    assert sessions.feedback == [(1, 9, -1)]


# ── StreamAnswer ─────────────────────────────────────────────────────


def _aiter(items):
    async def _gen():
        for item in items:
            yield item
    return _gen()


class FakeStreamLlm:
    def __init__(self, events=None, error=None):
        self.events = events or []
        self.error = error

    async def stream(self, *, messages, model, temperature, max_tokens):
        self.messages = messages
        if self.error:
            raise self.error
        return _aiter(self.events)


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Usage:
    prompt_tokens = 11
    completion_tokens = 7


class _Event:
    def __init__(self, content=None, usage=None):
        self.choices = [_Choice(content)] if content is not None else []
        self.usage = usage


def _parse(frames):
    out = []
    for frame in frames:
        event = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                out.append((event, json.loads(line[5:].strip())))
    return out


def test_stream_answer_grounded_flow_with_usage_backfill():
    llm = FakeStreamLlm(events=[_Event(usage=_Usage()), _Event("答案"), _Event("片段")])
    ledger = FakeLedger()
    use_case = StreamAnswer(llm, ledger, model="m", api_key="k", temperature=0.3, max_tokens=512)
    frames = list(asyncio.run(_collect(use_case.execute("问题", [], [_chunk("A.pdf", 1, 0.9)], [_chunk("A.pdf", 1, 0.9)], 42))))
    parsed = _parse(frames)
    assert parsed[0] == ("sources", [_chunk("A.pdf", 1, 0.9)])
    assert parsed[1][0] == "mode" and parsed[1][1] == {"mode": "grounded"}
    assert parsed[2] == ("delta", {"text": "答案"})
    assert parsed[-1] == ("done", {"finish_reason": "stop", "usage": {"prompt_tokens": 11, "completion_tokens": 7}, "mode": "grounded"})
    assert ledger.usage_backfills == [(42, {"prompt_tokens": 11, "completion_tokens": 7})]


async def _collect(gen):
    out = []
    async for item in gen:
        out.append(item)
    return out


def test_stream_answer_fallback_hides_sources():
    llm = FakeStreamLlm(events=[_Event("通用回答")])
    frames = list(asyncio.run(_collect(StreamAnswer(llm, FakeLedger(), model="m", api_key="k", temperature=0, max_tokens=1).execute("问题", [], [], [], None))))
    parsed = _parse(frames)
    assert parsed[0] == ("sources", [])
    assert parsed[1][1] == {"mode": "fallback"}
    assert any(e == "delta" for e, _ in parsed)


def test_stream_answer_without_api_key_emits_error():
    frames = list(asyncio.run(_collect(StreamAnswer(FakeStreamLlm(), FakeLedger(), model="m", api_key="", temperature=0, max_tokens=1).execute("问题", [], [], [], 1))))
    parsed = _parse(frames)
    assert parsed[0] == ("sources", [])  # no chunks yet at that point
    assert parsed[1] == ("mode", {"mode": "fallback"})
    assert parsed[2][0] == "error"
    assert "LLM_API_KEY" in parsed[2][1]["detail"]


def test_stream_answer_llm_failure_emits_error_event():
    llm = FakeStreamLlm(error=RuntimeError("boom"))
    ledger = FakeLedger()
    frames = list(asyncio.run(_collect(StreamAnswer(llm, ledger, model="m", api_key="k", temperature=0, max_tokens=1).execute("问题", [], [_chunk("A.pdf", 1, 0.9)], [], 5))))
    parsed = _parse(frames)
    assert parsed[2][0] == "error"
    assert "生成失败" in parsed[2][1]["detail"]
    assert ledger.usage_backfills == []  # no usage backfill on failure


# ── persist_after_stream ─────────────────────────────────────────────


def test_persist_after_stream_collects_deltas_and_mode():
    async def stream():
        yield 'event: mode\ndata: {"mode": "grounded"}\n\n'
        yield 'event: delta\ndata: {"text": "回答"}\n\n'.encode()
        yield 'event: done\ndata: {"finish_reason": "stop"}\n\n'

    tr = FakeTranscripts()
    frames = list(asyncio.run(_collect(persist_after_stream(tr, stream(), session_id=3, sources_json='[{"document_name": "A.pdf"}]'))))
    assert len(frames) == 3  # passthrough untouched
    assert tr.replies == [(3, "回答", '[{"document_name": "A.pdf"}]', "grounded")]
