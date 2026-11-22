"""接口契约测试（T2 / T4）：字段名、错误码、口径。

这些断言都是**照着 `契约/API.md` 抄下来的**，不是照着实现抄的：
实现改了字段而契约没改，这里会红；契约改了而实现没跟，这里也会红。
"""
from __future__ import annotations

BRIEF_KEYS = {
    "clause_id", "doc_id", "doc_title", "clause_no", "clause_index", "chapter",
    "snippet", "score", "status", "effective_date", "source_url",
}
DETAIL_KEYS = {
    "clause_id", "doc_id", "doc_title", "clause_no", "clause_index", "chapter", "text",
    "doc_type", "domain", "org", "doc_number", "publish_date", "effective_date",
    "expire_date", "status", "source_url", "fetched_at",
}
SEARCH_ITEM_KEYS = {
    "clause_id", "doc_title", "clause_no", "snippet", "highlight", "status",
    "source_url", "score",
}
FILTER_AXES = {"domain", "status", "org", "doc_type", "topic_tags"}


def _ok(resp) -> dict:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "ok"
    return body["data"]


def test_health_shape(client):
    data = _ok(client.get("/api/v1/health"))
    assert data["service"] == "ok"
    assert data["mode"] == "mock"                 # mock 必须自曝，不能冒充真库
    assert data["mysql"]["schema"] == "ok"        # B15：建表成功
    assert data["milvus"]["status"] == "mock"
    assert data["milvus"]["row_count"] > 0


def test_meta_filters_doc_level(client):
    data = _ok(client.get("/api/v1/meta/filters"))
    assert set(data) == FILTER_AXES | {"date_range"}
    # 文档级口径：五个轴合计都是 159（topic_tags 多选，例外）
    assert sum(x["count"] for x in data["domain"]) == 159
    assert sum(x["count"] for x in data["status"]) == 159
    assert sum(x["count"] for x in data["doc_type"]) == 159
    # 与 API.md §5 的示例数字逐项对齐
    assert {x["value"]: x["count"] for x in data["status"]} == {"有效": 155, "尚未生效": 3, "已废止": 1}
    assert len(data["org"]) == 23
    assert len(data["topic_tags"]) == 22
    assert sum(x["count"] for x in data["topic_tags"]) > 159
    assert data["date_range"] == {"min": 19930301, "max": 20270301}


def test_clause_detail_fields(client, fixtures):
    cid = fixtures["details"][0]["clause_id"]
    data = _ok(client.get(f"/api/v1/clause/{cid}"))
    assert set(data) == DETAIL_KEYS
    # B3：v1 里 fetched_at 恒 0；doc_number/publish_date 恒 None
    assert data["fetched_at"] > 0
    assert data["publish_date"] is None or isinstance(data["publish_date"], int)
    assert data["expire_date"] is None            # 159/159 全空，这是正确值


def test_clause_not_found(client):
    resp = client.get("/api/v1/clause/NOT-EXIST-A1")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


def test_related_shape(client, fixtures):
    cid = fixtures["details"][0]["clause_id"]
    data = _ok(client.get(f"/api/v1/clause/{cid}/related?top_k=3"))
    assert data["clause_id"] == cid
    assert len(data["related"]) <= 3
    for item in data["related"]:
        assert set(item) == BRIEF_KEYS | {"relation_type"}
        assert item["relation_type"] in {"同条", "同章", "引用", "相似"}


def test_search_shape_and_total(client):
    data = _ok(client.post("/api/v1/search", json={"keyword": "资本", "page": 1, "size": 5}))
    assert set(data) == {"total", "page", "size", "items"}
    assert data["total"] <= 200                   # §2：候选上限 200
    assert len(data["items"]) <= 5
    for item in data["items"]:
        assert set(item) == SEARCH_ITEM_KEYS
        assert "<em>" in item["highlight"]


def test_param_error_is_40001_not_422(client):
    resp = client.post("/api/v1/chat", json={"question": "", "session_id": "s1"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001 and body["data"] is None


def test_unknown_route_envelope(client):
    resp = client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40400


def test_history_unknown_session(client):
    resp = client.get("/api/v1/history", params={"session_id": "sess_does_not_exist"})
    assert resp.status_code == 404
    assert resp.json()["code"] == 40402


def test_history_created_at_is_14_digits(client, session_id):
    data = _ok(client.get("/api/v1/history", params={"session_id": session_id}))
    assert data["total"] >= 1
    item = data["items"][0]
    assert len(str(item["created_at"])) == 14     # B4
    assert set(item) == {"qa_id", "question", "answer", "score", "created_at", "sources"}
    if item["sources"]:
        assert set(item["sources"][0]) == {"clause_id", "clause_no", "doc_title"}


def test_history_delete_requires_exactly_one_param(client):
    resp = client.delete("/api/v1/history")
    assert resp.status_code == 400 and resp.json()["code"] == 40001
    resp = client.delete("/api/v1/history", params={"session_id": "a", "qa_id": 1})
    assert resp.status_code == 400 and resp.json()["code"] == 40001


def test_chat_sse_order_and_single_done(client):
    sid = "sess_sse_test"
    events: list[tuple[str, dict]] = []
    import json

    with client.stream("POST", "/api/v1/chat",
                       json={"question": "商业银行资本充足率", "session_id": sid}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                name, data = None, None
                for line in block.splitlines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                if name:
                    events.append((name, data))

    names = [n for n, _ in events]
    assert names.count("done") == 1               # B1：只能有一个 done
    assert "sources" in names and "score" in names
    assert names.index("sources") < names.index("done")
    done = dict(events)["done"]
    assert done["qa_id"] > 0                      # 落库成功，真 qa_id
    assert done["elapsed_ms"] >= 0


def test_chat_refusal_has_empty_sources(client):
    import json

    sid = "sess_refusal_test"
    events = []
    with client.stream("POST", "/api/v1/chat",
                       json={"question": "这个问题的答案库里没有请拒答", "session_id": sid}) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                name, data = None, None
                for line in block.splitlines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                if name:
                    events.append((name, data))
    sources = dict(events)["sources"]["sources"]
    assert sources == []


def test_chat_error_event_is_terminal(client):
    import json

    events = []
    with client.stream("POST", "/api/v1/chat",
                       json={"question": "触发一个错误", "session_id": "sess_err"}) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                name, data = None, None
                for line in block.splitlines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                if name:
                    events.append((name, data))
    names = [n for n, _ in events]
    assert "error" in names
    assert "done" not in names                    # §1：error 之后不再有 done
