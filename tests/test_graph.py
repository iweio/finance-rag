"""图谱服务测试（T9）：直接测 `backend/graph_service.py`，不需要 fastapi。

图产物在 `kg/out/serving/`；没有产物时整组跳过（不把 CI 卡在数据上）。
接口层（/graph/*）的形状由 tests/test_api.py 的契约断言覆盖。
"""
from __future__ import annotations

import os

import pytest

from backend.graph_service import GraphData, GraphDataError

SERVING = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "kg", "out", "serving")
pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(SERVING, "manifest.json")),
    reason="kg/out/serving 不存在，先跑 kg/build_graph.py + kg/export_serving.py",
)


def test_stats_totals():
    st = GraphData().stats()
    assert st["total_nodes"] > 9000
    assert st["total_edges"] > 30000
    assert st["node_counts"]["Clause"] == 8156
    assert st["has_embeddings"] in (True, False)


def test_neighbors_one_hop():
    data = GraphData()
    sub = data.subgraph("NFRA-016-A26", hops=1, limit=20)
    assert sub["focus"] == "C:NFRA-016-A26"
    assert sub["node_count"] >= 2
    # 1 跳里至少有：相邻条文 + 所属章节 + 所属法规
    types = {n["type"] for n in sub["nodes"]}
    assert {"Clause", "Doc"} <= types
    edge_types = {e["type"] for e in sub["edges"]}
    assert "IN_DOC" in edge_types
    assert "NEXT" in edge_types


def test_edge_type_filter():
    data = GraphData()
    sub = data.subgraph("NFRA-016-A26", hops=1, limit=20, edge_types=["IN_DOC"])
    assert {e["type"] for e in sub["edges"]} == {"IN_DOC"}
    assert all(n["type"] in ("Clause", "Doc") for n in sub["nodes"])


def test_limit_and_truncation():
    data = GraphData()
    sub = data.subgraph("NFRA-016-A26", hops=2, limit=5)
    assert sub["node_count"] <= 5
    assert sub["truncated"] is True


def test_unknown_clause_raises():
    with pytest.raises(GraphDataError) as exc:
        GraphData().subgraph("NOT-EXIST-A1")
    assert exc.value.code == "NOT_FOUND"


def test_unknown_edge_type_raises():
    with pytest.raises(GraphDataError) as exc:
        GraphData().subgraph("NFRA-016-A26", edge_types=["BOGUS"])
    assert exc.value.code == "INVALID"


def test_accepts_prefixed_and_raw_ids():
    data = GraphData()
    assert data.subgraph("C:NFRA-016-A26")["focus"] == "C:NFRA-016-A26"
    assert data.subgraph("NFRA-016-A26")["focus"] == "C:NFRA-016-A26"
