"""认证与归属（T8）：§9 #7 的裁决落地验收。

覆盖：注册重名 40901、密码错 40101、参数错 40001、跨用户查会话 40301、收藏按用户隔离。

**一条容易搞反的规则**：匿名（不带 token）仍然可以收藏与查历史，写入 `user_id=NULL`。
这样 8 个既有接口的契约不变；「未登录点收藏要弹登录」是**前端体验**，不是后端 401。
"""
from __future__ import annotations

import uuid


def test_register_and_login(client):
    name = f"u{uuid.uuid4().hex[:8]}"
    data = client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"}).json()["data"]
    assert data["username"] == name and data["user_id"] > 0

    data = client.post("/api/v1/auth/login", json={"username": name, "password": "secret123"}).json()["data"]
    assert data["token"] and data["expires_in"] > 0


def test_register_duplicate_40901(client):
    name = f"u{uuid.uuid4().hex[:8]}"
    assert client.post("/api/v1/auth/register",
                       json={"username": name, "password": "secret123"}).status_code == 200
    resp = client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
    assert resp.status_code == 409
    assert resp.json()["code"] == 40901


def test_login_wrong_password_40101(client):
    name = f"u{uuid.uuid4().hex[:8]}"
    client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
    resp = client.post("/api/v1/auth/login", json={"username": name, "password": "wrong-pass"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


def test_register_short_password_40001(client):
    resp = client.post("/api/v1/auth/register", json={"username": "abc", "password": "123"})
    assert resp.status_code == 400 and resp.json()["code"] == 40001


def test_anonymous_chat_still_works(client):
    """契约承诺：不带 token 时 8 个既有接口行为不变（聊天/检索/详情都可用）。"""
    with client.stream("POST", "/api/v1/chat",
                       json={"question": "资本充足率", "session_id": "sess_anon"}) as r:
        assert r.status_code == 200
        for _ in r.iter_text():
            pass
    assert client.post("/api/v1/search", json={"keyword": "资本"}).status_code == 200


def test_cross_user_session_forbidden(client):
    """A 的 token 查 B 的 session_id → 40301。"""
    def make_user():
        name = f"u{uuid.uuid4().hex[:8]}"
        client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
        tok = client.post("/api/v1/auth/login",
                          json={"username": name, "password": "secret123"}).json()["data"]["token"]
        return {"Authorization": f"Bearer {tok}"}

    a, b = make_user(), make_user()
    sid = "sess_b_" + uuid.uuid4().hex[:8]
    with client.stream("POST", "/api/v1/chat",
                       json={"question": "资本", "session_id": sid}, headers=b) as r:
        for _ in r.iter_text():
            pass

    resp = client.get("/api/v1/history", params={"session_id": sid}, headers=a)
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301

    resp = client.get("/api/v1/history", params={"session_id": sid}, headers=b)
    assert resp.status_code == 200


def test_favorite_isolated_per_user(client):
    users = []
    for _ in range(2):
        name = f"u{uuid.uuid4().hex[:8]}"
        client.post("/api/v1/auth/register", json={"username": name, "password": "secret123"})
        tok = client.post("/api/v1/auth/login",
                          json={"username": name, "password": "secret123"}).json()["data"]["token"]
        users.append({"Authorization": f"Bearer {tok}"})

    cid = "NFRA-016-A26"
    r = client.post("/api/v1/favorite", json={"clause_id": cid}, headers=users[0])
    assert r.status_code == 200
    fid = r.json()["data"]["favorite_id"]

    # 幂等：同一条再收藏返回同一个 id
    assert client.post("/api/v1/favorite", json={"clause_id": cid},
                       headers=users[0]).json()["data"]["favorite_id"] == fid

    # 另一个用户看不到
    data = client.get("/api/v1/favorite", headers=users[1]).json()["data"]
    assert data["total"] == 0

    data = client.get("/api/v1/favorite", headers=users[0]).json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    # B5：补了 doc_title / clause_no
    assert set(item) == {"favorite_id", "clause_id", "doc_title", "clause_no", "created_at"}
    assert item["doc_title"] and item["clause_no"]
    assert len(str(item["created_at"])) == 14

    # 删别人的收藏 → 40301
    assert client.delete("/api/v1/favorite", params={"favorite_id": fid},
                         headers=users[1]).status_code == 403
    assert client.delete("/api/v1/favorite", params={"favorite_id": fid},
                         headers=users[0]).json()["data"]["deleted"] == 1


def test_favorite_requires_exactly_one(client, auth_headers):
    resp = client.post("/api/v1/favorite", json={}, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["code"] == 40001
    resp = client.post("/api/v1/favorite", json={"clause_id": "X-1", "qa_id": 1}, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["code"] == 40001


def test_favorite_get_without_token_returns_anonymous(client):
    r = client.get("/api/v1/favorite")
    assert r.status_code == 200
    assert r.json()["data"]["total"] >= 0
