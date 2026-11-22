"""Mock 引擎：无 Milvus、无模型时也能把 8 个接口真跑起来（B19）。

**不是给交付演示用的**。用途只有三个：
1. 本机/CI 没有 Docker 时，能验证 HTTP 层的 JSON 形状与字段名；
2. 前端联调时后端不用等 8156 条向量库；
3. pytest 里做确定性数据源（不依赖网络、不依赖 GPU）。

切换：`RAG_MODE=mock`（默认 real）。mock 模式下 `/health` 会带 `"mode": "mock"`，
防止有人拿 mock 的健康检查当「知识库已就位」的证据。

数据来自 `frontend/src/api/mock/fixtures.json`（由 `脚本/_export_frontend_fixtures.py`
从 chunks + 语料元数据.xlsx 生成），**不是手编的假数据**。
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Iterator

from rag_core.errors import RagError
from rag_core.models import (
    ClauseBrief, ClauseDetail, Event, Filters, RelatedClause, SearchItem, SearchResult,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FIXTURES = os.path.join(ROOT, "frontend", "src", "api", "mock", "fixtures.json")

REFUSAL_TEXT = (
    "抱歉，当前知识库中未找到与您问题直接相关的制度条文，无法给出准确回答。"
    "建议您调整问题措辞，或咨询专业法律人员。"
)
_DELTA_DELAY = float(os.environ.get("MOCK_DELTA_DELAY", "0.08"))


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；;！？\n])", text)
    return [p for p in parts if p.strip()]


@dataclass
class _Fixture:
    details: dict[str, dict]
    search_items: list[dict]
    related: list[dict]
    doc_meta: dict[str, dict]
    health: dict


def _load(path: str) -> _Fixture:
    if not os.path.isfile(path):
        raise RuntimeError(
            f"mock fixtures 不存在：{path}\n"
            "先跑：python 脚本/_export_frontend_fixtures.py"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    details = {d["clause_id"]: d for d in data.get("details", [])}
    return _Fixture(
        details=details,
        search_items=data.get("search_items", []),
        related=data.get("related", []),
        doc_meta=data.get("doc_meta", {}),
        health=data.get("health", {}),
    )


class MockRagEngine:
    """与 RagEngine 同签名（4 个方法）+ get_clauses 批量方法。"""

    def __init__(self, fixtures_path: str | None = None) -> None:
        self._fx = _load(fixtures_path or os.environ.get("MOCK_FIXTURES") or DEFAULT_FIXTURES)

    # ---------- 内部工具 ----------

    @staticmethod
    def _to_detail(d: dict) -> ClauseDetail:
        return ClauseDetail(
            clause_id=d["clause_id"], doc_id=d["doc_id"], doc_title=d["doc_title"],
            clause_no=d["clause_no"], clause_index=int(d["clause_index"]),
            chapter=d.get("chapter"), text=d.get("text", ""),
            doc_type=d.get("doc_type", ""), domain=d.get("domain", ""),
            org=d.get("org", ""), doc_number=d.get("doc_number"),
            publish_date=d.get("publish_date"), effective_date=d.get("effective_date"),
            expire_date=d.get("expire_date"), status=d.get("status", ""),
            source_url=d.get("source_url"), fetched_at=int(d.get("fetched_at") or 0),
            chunk_type=d.get("chunk_type") or "clause",
        )

    @staticmethod
    def _to_brief(d: dict, score: float = 0.9) -> ClauseBrief:
        text = d.get("text", "")
        return ClauseBrief(
            clause_id=d["clause_id"], doc_id=d["doc_id"], doc_title=d["doc_title"],
            clause_no=d["clause_no"], clause_index=int(d["clause_index"]),
            chapter=d.get("chapter"),
            snippet=text[:200] + "..." if len(text) > 200 else text,
            score=score, status=d.get("status", ""),
            effective_date=d.get("effective_date"), source_url=d.get("source_url"),
        )

    def _match_filters(self, doc_id: str, filters: Filters | None) -> bool:
        if filters is None:
            return True
        meta = self._fx.doc_meta.get(doc_id, {})
        if filters.domain and meta.get("domain") != filters.domain:
            return False
        if filters.status and meta.get("status") != filters.status:
            return False
        if filters.org and filters.org not in str(meta.get("org") or ""):
            return False
        return True

    # ---------- 契约 4 方法 ----------

    def answer_stream(self, question: str, filters: Filters | None = None,
                      session_id: str = "") -> Iterator[Event]:
        t0 = time.time()
        q = (question or "").strip()
        if not q:
            raise RagError("RETRIEVE_FAILED", "问题不能为空")

        # 测试钩子：让前端能点出「服务端错误」与「拒答」两条分支
        if "错误" in q:
            yield Event("error", {"code": 50002, "message": "（mock）生成失败，用于测试错误态"})
            return
        if "拒答" in q:
            yield Event("delta", {"text": REFUSAL_TEXT})
            yield Event("sources", {"sources": []})
            yield Event("score", {"score": 0.12})
            yield Event("done", {"qa_id": -1, "elapsed_ms": int((time.time() - t0) * 1000)})
            return

        pool = [d for d in self._fx.details.values() if self._match_filters(d["doc_id"], filters)]
        if not pool:
            pool = list(self._fx.details.values())
        main = pool[0]
        answer = f"依据《{main['doc_title']}》{main['clause_no']}：{main.get('text', '')}"
        for sentence in _split_sentences(answer)[:15]:
            yield Event("delta", {"text": sentence})
            time.sleep(_DELTA_DELAY)

        sources = [self._to_brief(d, 0.91 - i * 0.03) for i, d in enumerate(pool[:3])]
        yield Event("sources", {"sources": [self._brief_dict(b) for b in sources]})
        yield Event("score", {"score": 0.91})
        yield Event("done", {"qa_id": -1, "elapsed_ms": int((time.time() - t0) * 1000)})

    @staticmethod
    def _brief_dict(b: ClauseBrief) -> dict:
        """与 generator._sources_data 同口径：ClauseBrief 的 11 个字段，不含 chunk_type。"""
        return {
            "clause_id": b.clause_id, "doc_id": b.doc_id, "doc_title": b.doc_title,
            "clause_no": b.clause_no, "clause_index": b.clause_index, "chapter": b.chapter,
            "snippet": b.snippet, "score": b.score, "status": b.status,
            "effective_date": b.effective_date, "source_url": b.source_url,
        }

    def search(self, keyword: str, filters: Filters | None, page: int, size: int) -> SearchResult:
        kw = (keyword or "").strip()
        if not kw:
            raise RagError("RETRIEVE_FAILED", "关键词不能为空")
        low = kw.lower()
        hits = []
        for item in self._fx.search_items:
            meta = self._fx.doc_meta.get(item.get("doc_id", ""), {})
            hay = f"{item.get('doc_title', '')}{item.get('clause_no', '')}{item.get('snippet', '')}"
            if low not in hay.lower():
                continue
            if not self._match_filters(item.get("doc_id", ""), filters):
                continue
            hits.append(item)

        total = min(len(hits), 200)  # 契约 §2：total 是候选数、上限 200
        start = (page - 1) * size
        page_items = hits[start:start + size]
        items = [
            SearchItem(
                clause_id=h["clause_id"], doc_title=h.get("doc_title", ""),
                clause_no=h.get("clause_no", ""), snippet=h.get("snippet", ""),
                # 高亮必须按本次 keyword 现算，与 retriever._make_highlight 同口径。
                # fixtures 里的 highlight 是按烘焙时那个关键词烤的，沿用会让
                # 「搜 A 高亮 B」—— 真实路径不会有这个行为，mock 也不该有。
                highlight=self._highlight(h.get("snippet", ""), kw),
                status=h.get("status", ""), source_url=h.get("source_url"),
                score=float(h.get("score") or 0.0),
            )
            for h in page_items
        ]
        return SearchResult(total=total, page=page, size=size, items=items)

    def get_clause(self, clause_id: str) -> ClauseDetail:
        d = self._fx.details.get(clause_id)
        if d is None:
            raise RagError("NOT_FOUND", f"条文 {clause_id} 不存在")
        return self._to_detail(d)

    def get_related_clauses(self, clause_id: str, top_k: int = 5) -> list[RelatedClause]:
        base = self._fx.details.get(clause_id)
        if base is None:
            raise RagError("NOT_FOUND", f"条文 {clause_id} 不存在")
        out: list[RelatedClause] = []
        for raw in self._fx.related:
            if len(out) >= top_k:
                break
            b = self._to_brief(raw, float(raw.get("score") or 0.8))
            out.append(RelatedClause(**vars(b), relation_type=raw.get("relation_type", "相似")))
        return out

    # ---------- 增补：批量 ----------

    def get_clauses(self, clause_ids: list[str]) -> list[ClauseDetail]:
        return [self.get_clause(cid) for cid in dict.fromkeys(clause_ids) if cid in self._fx.details]

    # ---------- health ----------

    def health_snapshot(self) -> dict:
        h = dict(self._fx.health)
        h["mode"] = "mock"
        return h

    @staticmethod
    def _highlight(text: str, keyword: str) -> str:
        esc, esk = html.escape(text), html.escape(keyword)
        return esc.replace(esk, f"<em>{esk}</em>") if esk else esc
