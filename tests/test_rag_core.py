"""rag_core 的纯逻辑测试（不需要 Milvus / 模型）。

跑的是三块真逻辑：
1. `Filters.to_milvus_expr` —— 拼错一个引号，Milvus 就报语法错；
2. `generate_stream` —— SSE 事件顺序、引用越界校验、拒答兜底（把 `_call_llm` 换成假的）；
3. `RagEngine.get_clause` 的字段映射 —— v1 这里把 4 个字段硬编码成 None/0（B3）。
"""
from __future__ import annotations

import rag_core.generator as generator
from rag_core.errors import RagError
from rag_core.models import ClauseBrief, Filters
from rag_core.service import RagEngine


def _clause(i: int = 1, text: str = "第一条 测试条文。", score: float = 0.9) -> ClauseBrief:
    return ClauseBrief(
        clause_id=f"NFRA-016-A{i}", doc_id="NFRA-016", doc_title="商业银行资本管理办法",
        clause_no=f"第{i}条", clause_index=i, chapter="第一章 总则",
        snippet=text, score=score, status="有效", effective_date=20240101,
        source_url="https://www.gov.cn/x",
    )


def test_filters_to_milvus_expr():
    assert Filters().to_milvus_expr() is None
    assert Filters(status="有效").to_milvus_expr() == 'status == "有效"'
    expr = Filters(domain="银行", status="有效", org="中国人民银行").to_milvus_expr()
    assert expr == 'status == "有效" and domain == "银行" and org == "中国人民银行"'


def test_generate_stream_event_order(monkeypatch):
    monkeypatch.setattr(generator, "_call_llm", lambda prompt: iter(["《商业银行资本管理办法》[1] 第一条 测试。"]))
    events = list(generator.generate_stream("测试问题", [_clause()]))
    names = [e.event for e in events]
    assert names[-2:] == ["score", "done"]
    assert names.count("done") == 1
    assert names.index("sources") < names.index("score") < names.index("done")
    assert events[0].event == "delta"
    assert len(events[1].data["sources"]) == 1


def test_generate_stream_refuses_without_clauses():
    events = list(generator.generate_stream("无依据的问题", []))
    assert [e.event for e in events] == ["delta", "sources", "score", "done"]
    assert events[1].data["sources"] == []
    assert events[2].data["score"] == 0.0


def test_generate_stream_out_of_range_citation_falls_back(monkeypatch):
    # 只有 1 条上下文，却引用 [9] → 越界 → 重生成一次仍越界 → 降级拒答
    bad = "《某法规》[9] 第一条 越界引用。"
    monkeypatch.setattr(generator, "_call_llm", lambda prompt: iter([bad]))
    events = list(generator.generate_stream("测试", [_clause()]))
    names = [e.event for e in events]
    assert names == ["delta", "sources", "score", "done"]
    assert "无法给出准确回答" in events[0].data["text"]
    assert events[1].data["sources"] != []       # 拒答时仍带已检出的条文（前端可展示）


class _StubRetriever:
    def __init__(self) -> None:
        self.hit = {
            "clause_id": "NFRA-016-A26", "doc_id": "NFRA-016",
            "doc_title": "商业银行资本管理办法", "clause_no": "第二十六条",
            "clause_index": 26, "chapter": "第二章 资本监管要求",
            "text": "第二十六条 商业银行各级资本充足率不得低于如下最低要求：",
            "doc_type": "部门规章", "domain": "银行", "org": "国家金融监督管理总局",
            "doc_number": "国家金融监督管理总局令2023年第4号",
            "publish_date": 20231026, "effective_date": 20240101, "expire_date": None,
            "status": "有效", "source_url": "https://www.gov.cn/x", "fetched_at": 20261027,
        }

    def get_clause(self, clause_id: str) -> dict:
        if clause_id != self.hit["clause_id"]:
            raise RagError("NOT_FOUND", f"条文 {clause_id} 不存在")
        return dict(self.hit)

    def get_related(self, clause_id: str, top_k: int):
        return [(dict(self.hit), "同章")]


def _engine_with_stub() -> RagEngine:
    eng = RagEngine.__new__(RagEngine)          # 跳过 __init__（它要连 Milvus）
    eng._retriever = _StubRetriever()           # type: ignore[attr-defined]
    return eng


def test_get_clause_maps_all_fields():
    """B3：17 个字段里，v1 的 doc_number/publish_date/expire_date/fetched_at 是 None/0。"""
    d = _engine_with_stub().get_clause("NFRA-016-A26")
    assert d.doc_number == "国家金融监督管理总局令2023年第4号"
    assert d.publish_date == 20231026
    assert d.effective_date == 20240101
    assert d.expire_date is None
    assert d.fetched_at == 20261027
    assert d.chunk_type == "clause"


def test_get_clause_not_found_raises():
    try:
        _engine_with_stub().get_clause("NOPE-1")
    except RagError as e:
        assert e.code == "NOT_FOUND"
    else:  # pragma: no cover
        raise AssertionError("应该抛 RagError")


def test_get_related_clauses_mapping():
    rel = _engine_with_stub().get_related_clauses("NFRA-016-A26", 3)
    assert len(rel) == 1 and rel[0].relation_type == "同章"
    assert rel[0].clause_id == "NFRA-016-A26"


def test_get_clauses_batch_dedup():
    eng = _engine_with_stub()
    got = eng.get_clauses(["NFRA-016-A26", "NFRA-016-A26"])
    assert len(got) == 1                     # 去重后只查一次
    assert got[0].clause_id == "NFRA-016-A26"
