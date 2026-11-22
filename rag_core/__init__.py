"""rag_core — 金融制度知识库的 RAG 引擎。

对外只暴露 RagEngine 一个类，4 个方法对应 API.md 的前 4 个接口。
P3 的 FastAPI 直接 import 这个包，不感知 Milvus、embedding、rerank 的内部实现。
"""
from rag_core.service import RagEngine
from rag_core.models import (
    Filters, ClauseBrief, RelatedClause, ClauseDetail,
    SearchResult, SearchItem, Event,
)
from rag_core.errors import RagError

__all__ = [
    "RagEngine", "Filters", "ClauseBrief", "RelatedClause", "ClauseDetail",
    "SearchResult", "SearchItem", "Event", "RagError",
]
