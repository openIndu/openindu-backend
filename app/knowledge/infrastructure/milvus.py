"""Milvus vector search service used by MCP tools."""
from typing import Any

from app.core.config import settings


class MilvusService:
    """Lazy Milvus client wrapper.

    The heavy embedding/model dependencies are initialized only when a search is requested,
    keeping the Web REST API startup lightweight.
    """

    def __init__(self):
        self._collection = None
        self._embedding_model = None

    def _ensure_ready(self):
        if self._collection is not None and self._embedding_model is not None:
            return
        from pymilvus import Collection, connections
        from sentence_transformers import SentenceTransformer

        connections.connect(alias="default", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
        self._collection = Collection(settings.MILVUS_COLLECTION)
        self._collection.load()
        self._embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL, device="cpu")

    def search(self, query: str, top_k: int = 5, where_filter: dict[str, Any] | None = None) -> list[dict]:
        self._ensure_ready()
        embedding = self._embedding_model.encode([query], normalize_embeddings=True)
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        expr = None
        if where_filter:
            expr = " and ".join(f'{key} == "{value}"' for key, value in where_filter.items() if value)
        results = self._collection.search(
            data=embedding,
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 128}},
            limit=top_k,
            expr=expr or None,
            output_fields=["text", "document_name", "brand", "category", "page", "chunk_id"],
        )
        items = []
        for hits in results:
            for hit in hits:
                # pymilvus 2.4 的 Hit.entity.get(k) 只接受单个 key 参数（不像 dict.get 那样
                # 可接受默认值），多传一个参数会触发 TypeError。统一用 `or` 兜底缺失字段。
                ent = hit.entity
                items.append({
                    "id": hit.id,
                    "score": round(hit.score, 4),
                    "text": ent.get("text") or "",
                    "metadata": {
                        "document_name": ent.get("document_name") or "",
                        "brand": ent.get("brand") or "",
                        "category": ent.get("category") or "",
                        "page": ent.get("page") or 0,
                        "chunk_id": ent.get("chunk_id") or 0,
                    },
                })
        return items

    def delete_by_document(self, document_name: str) -> int:
        self._ensure_ready()
        expr = f'document_name == "{document_name}"'
        rows = self._collection.query(expr=expr, output_fields=["id"])
        if rows:
            self._collection.delete(expr=expr)
        return len(rows)


milvus_service = MilvusService()
