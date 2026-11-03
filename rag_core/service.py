"""RagEngine — P1 交付给 P3 的唯一入口。

4 个方法一一对应 API.md 的前 4 个接口：
  answer_stream       → POST /api/v1/chat
  search              → POST /api/v1/search
  get_clause          → GET  /api/v1/clause/{clause_id}
  get_related_clauses → GET  /api/v1/clause/{clause_id}/related

P3 只依赖这个类，不感知 Milvus、embedding、rerank 的内部实现。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterator

from rag_core.errors import RagError
from rag_core.models import (
    Filters, ClauseBrief, RelatedClause, ClauseDetail,
    SearchResult, SearchItem, Event,
)
from rag_core.retriever import Retriever
from rag_core.generator import generate_stream


class RagEngine:
    """金融制度知识库 RAG 引擎。"""

    def __init__(self) -> None:
        self._retriever = Retriever()

    def answer_stream(
        self,
        question: str,
        filters: Filters | None = None,
        session_id: str = "",
    ) -> Iterator[Event]:
        """问答（流式）。先检索，再调 LLM 生成，逐事件 yield。"""
        if not question or not question.strip():
            raise RagError("RETRIEVE_FAILED", "问题不能为空")
        if len(question) > 500:
            raise RagError("RETRIEVE_FAILED", "问题不能超过 500 字")

        clauses = self._retriever.retrieve(question, filters, top_k=5)
        yield from generate_stream(question, clauses)

    def search(
        self,
        keyword: str,
        filters: Filters | None = None,
        page: int = 1,
        size: int = 10,
    ) -> SearchResult:
        """关键词检索。混合检索（向量 + BM25 + RRF + rerank）。"""
        if not keyword or not keyword.strip():
            raise RagError("RETRIEVE_FAILED", "关键词不能为空")
        if len(keyword) > 100:
            raise RagError("RETRIEVE_FAILED", "关键词不能超过 100 字")
        if page < 1:
            page = 1
        if size < 1 or size > 50:
            size = 10

        total, items = self._retriever.search(keyword, filters, page, size)
        return SearchResult(total=total, page=page, size=size, items=items)

    def get_clause(self, clause_id: str) -> ClauseDetail:
        """条文详情。查不到抛 RagError("NOT_FOUND", ...)。"""
        if not clause_id or not clause_id.strip():
            raise RagError("NOT_FOUND", "clause_id 不能为空")

        hit = self._retriever.get_clause(clause_id)
        return ClauseDetail(
            clause_id=hit["clause_id"],
            doc_id=hit.get("doc_id", ""),
            doc_title=hit.get("doc_title", ""),
            clause_no=hit.get("clause_no", ""),
            clause_index=hit.get("clause_index", 0),
            chapter=hit.get("chapter"),
            text=hit.get("text", ""),
            doc_type=hit.get("doc_type", ""),
            domain=hit.get("domain", ""),
            org=hit.get("org", ""),
            doc_number=None,
            publish_date=None,
            effective_date=hit.get("effective_date"),
            expire_date=None,
            status=hit.get("status", ""),
            source_url=hit.get("source_url"),
            fetched_at=0,
        )

    def get_related_clauses(
        self, clause_id: str, top_k: int = 5,
    ) -> list[RelatedClause]:
        """条文关联查询。"""
        if not clause_id or not clause_id.strip():
            raise RagError("NOT_FOUND", "clause_id 不能为空")
        if top_k < 1 or top_k > 20:
            top_k = 5

        related = self._retriever.get_related(clause_id, top_k)
        result = []
        for hit, rel_type in related:
            text = hit.get("text", "")
            snippet = text[:200] + "..." if len(text) > 200 else text
            result.append(RelatedClause(
                clause_id=hit["clause_id"],
                doc_id=hit.get("doc_id", ""),
                doc_title=hit.get("doc_title", ""),
                clause_no=hit.get("clause_no", ""),
                clause_index=hit.get("clause_index", 0),
                chapter=hit.get("chapter"),
                snippet=snippet,
                score=hit.get("score", 0.0),
                status=hit.get("status", ""),
                effective_date=hit.get("effective_date"),
                source_url=hit.get("source_url"),
                relation_type=rel_type,
            ))
        return result
