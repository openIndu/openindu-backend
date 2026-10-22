"""Knowledge-base retrieval with automatic brand scoping and retry."""
from typing import Any, Protocol

from app.knowledge.domain.answering import GROUNDED_SCORE_THRESHOLD
from app.knowledge.domain.query_rules import match_brand


class SearchGateway(Protocol):
    def search(self, query: str, top_k: int, where_filter: dict[str, Any] | None) -> list[dict]: ...


class BrandDirectory(Protocol):
    def registered_brands(self) -> list[tuple[str, str | None]]: ...


class Retriever:
    def __init__(self, search: SearchGateway, brands: BrandDirectory, *, default_top_k: int) -> None:
        self.search = search
        self.brands = brands
        self.default_top_k = default_top_k

    def retrieve(self, message: str, top_k: int | None = None, filters: dict | None = None) -> list[dict]:
        """Vector search over the knowledge base. Blocking — call via run_in_threadpool.

        The caller should first pass the user message through ``rewrite_query()``
        (with conversation history) to produce a self-contained search query;
        then call this function with the rewritten query.

        If the caller didn't already pin a brand (``filters["brand"]``), this
        auto-extracts one from the query text and narrows the ANN search to it —
        a wrong auto-extraction only costs one retry, never a missed answer:
        when the brand-filtered search comes back empty or below the grounded
        threshold, it's re-run unfiltered and that result is used instead. An
        explicit caller-supplied brand filter is trusted as-is (no retry) since
        that reflects deliberate user intent, not a guess.
        """
        where = {k: v for k, v in (filters or {}).items() if v}
        auto_extracted = False
        if not where.get("brand"):
            auto_brand = match_brand(self.brands.registered_brands(), message)
            if auto_brand:
                where["brand"] = auto_brand
                auto_extracted = True

        top_k = top_k or self.default_top_k
        chunks = self.search.search(message, top_k=top_k, where_filter=where or None)

        if auto_extracted and (not chunks or chunks[0]["score"] < GROUNDED_SCORE_THRESHOLD):
            unfiltered = {k: v for k, v in where.items() if k != "brand"}
            chunks = self.search.search(message, top_k=top_k, where_filter=unfiltered or None)

        return chunks
