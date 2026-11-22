"""pytest 公共夹具（T1）。

三个关键点：
1. **环境变量必须在 import app 之前设好**：`backend.database` 在模块导入时就读 `DATABASE_URL`，
   `backend.main` 在导入时读 `RAG_MODE` / `APP_ENV`。
2. 用 SQLite 跑，不连 MySQL（模型已加 `with_variant`，见 B18）。用临时文件而不是 `:memory:`，
   这样多个连接看到同一个库。
3. `RAG_MODE=mock`：不连 Milvus、不加载 bge-m3，测试跑在秒级。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_TMP = Path(tempfile.mkdtemp(prefix="rag_test_"))

os.environ["RAG_MODE"] = "mock"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["JWT_SECRET"] = "test-only-secret"
os.environ["APP_ENV"] = "test"
os.environ["MOCK_DELTA_DELAY"] = "0"          # 测试里不要 80ms/句的等待
os.environ.setdefault("OUTPUT_DIR", str(ROOT / "产出"))
os.environ.setdefault("MOCK_FIXTURES", str(ROOT / "frontend/src/api/mock/fixtures.json"))
os.environ["LLM_API_KEY"] = os.environ.get("LLM_API_KEY", "sk-test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def fixtures() -> dict:
    import json

    with open(os.environ["MOCK_FIXTURES"], encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def auth_headers(client) -> dict:
    """注册并登录一个随机用户，返回 Authorization 头。"""
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    r = client.post("/api/v1/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "secret123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


@pytest.fixture()
def session_id(client) -> str:
    """跑一次 chat 让 session 落库（历史/收藏接口依赖它）。"""
    import uuid

    sid = f"sess_{uuid.uuid4().hex[:10]}"
    with client.stream("POST", "/api/v1/chat",
                       json={"question": "商业银行资本充足率要求", "session_id": sid}) as r:
        assert r.status_code == 200
        for _ in r.iter_text():
            pass
    return sid
