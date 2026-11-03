"""RagEngine 的对外数据模型。

字段名与 `契约/SPEC.md` §2 的列名、`契约/API.md` 的响应 JSON、`契约/DB.md` §3 的 Milvus 字段名
逐字共用同一套叫法。改名即返工——前端 axios、Postman 集合、Milvus output_fields 全要跟着改。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterator


@dataclass
class Filters:
    """查询筛选条件。三个字段全部可空，None 表示不限。

    拼成 Milvus filter= 表达式时，None 的字段不加条件，
    不要默认补 status == "有效"（用户可能想查已废止条文）。
    """
    domain: str | None = None
    status: str | None = None
    org: str | None = None

    def to_milvus_expr(self) -> str | None:
        conds = []
        if self.status:
            conds.append(f'status == "{self.status}"')
        if self.domain:
            conds.append(f'domain == "{self.domain}"')
        if self.org:
            conds.append(f'org == "{self.org}"')
        return " and ".join(conds) if conds else None


@dataclass
class ClauseBrief:
    """条文摘要。/api/v1/chat 的 sources[] 与 /clause/{id}/related 共用此结构。"""
    clause_id: str
    doc_id: str
    doc_title: str
    clause_no: str
    clause_index: int
    chapter: str | None
    snippet: str
    score: float
    status: str
    effective_date: int | None
    source_url: str | None


@dataclass
class RelatedClause(ClauseBrief):
    """关联条文，比 ClauseBrief 多一个 relation_type。"""
    relation_type: str = ""


@dataclass
class ClauseDetail:
    """条文完整元数据卡片。17 个字段名 = SPEC §2 列名 + clause 级字段。"""
    clause_id: str
    doc_id: str
    doc_title: str
    clause_no: str
    clause_index: int
    chapter: str | None
    text: str
    doc_type: str
    domain: str
    org: str
    doc_number: str | None
    publish_date: int | None
    effective_date: int | None
    expire_date: int | None
    status: str
    source_url: str | None
    fetched_at: int


@dataclass
class SearchItem:
    """关键词检索结果项。比 ClauseBrief 少 4 个、多 1 个 highlight。"""
    clause_id: str
    doc_title: str
    clause_no: str
    snippet: str
    highlight: str
    status: str
    source_url: str | None
    score: float


@dataclass
class SearchResult:
    """关键词检索的响应。"""
    total: int
    page: int
    size: int
    items: list[SearchItem] = field(default_factory=list)


@dataclass
class Event:
    """answer_stream 的 SSE 事件。"""
    event: str
    data: dict
