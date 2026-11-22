"""认证：bcrypt 密码哈希 + JWT（§9 #7 裁决，B20）。

契约影响：接口从 8 个扩到 10 个（`/auth/register`、`/auth/login`），
`API.md §0.4` 新增 `40101 / 40301 / 40901`。**这是全篇唯一主动扩契约的地方。**

四条设计决定，改之前先看：
1. **匿名仍可用**：不带 token 时聊天/检索/详情照旧，收藏与历史按 `session_id`（v1 行为）。
   只有 `/history`、`/favorite` 在带 token 时会额外校验归属。
2. **`qa_history` 没有 `user_id` 列**（DB.md §2.2），历史归属只能借 `session.user_id` 关联，
   所以 `chat` 带 token 时要把 `session.user_id` 写上，**不要去改 DDL**。
3. **`JWT_SECRET` 生产必填**：`APP_ENV=production` 且没配 → 启动失败。
   v1 若做成"没配就随机生成"，重启一次全部登录态失效，表现是"刚登录就 401"。
4. **密码先 sha256 再 bcrypt**：bcrypt 只吃 72 字节，直接塞超长密码会被静默截断或抛错；
   base64(sha256(pw)) 固定 44 字节，顺带解决这个边界。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.errors import FORBIDDEN, UNAUTHORIZED, ApiError
from backend.models import User

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

_random_secret: str | None = None


def _secret() -> str:
    """取 JWT 密钥。生产缺变量直接抛，开发/测试随机生成并警告（重启即失效）。"""
    global _random_secret
    configured = os.environ.get("JWT_SECRET", "").strip()
    if configured:
        return configured
    if os.environ.get("APP_ENV", "").lower() == "production":
        raise RuntimeError(
            "APP_ENV=production 但 JWT_SECRET 未配置：拒绝用随机密钥启动"
            "（重启后所有登录态失效，表现为「刚登录就 401」）。请在 .env 里设置 JWT_SECRET。"
        )
    if _random_secret is None:
        _random_secret = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET 未配置，已生成临时随机密钥（仅开发可用，重启后 token 全部失效）")
    return _random_secret


def ensure_configured() -> None:
    """启动时调用一次，让生产环境缺密钥**当场**失败，而不是等到第一次登录。"""
    _secret()


# ---------------- 密码 ----------------

def _bcrypt():
    try:
        import bcrypt  # noqa: PLC0415 - 延迟导入，缺依赖时报错更可读
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise RuntimeError("缺 bcrypt：pip install 'bcrypt>=4.1'") from exc
    return bcrypt


def _prehash(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return _bcrypt().hashpw(_prehash(password), _bcrypt().gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _bcrypt().checkpw(_prehash(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------- JWT ----------------

def _jwt():
    try:
        import jwt  # noqa: PLC0415 - PyJWT
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise RuntimeError("缺 PyJWT：pip install 'PyJWT>=2.8'") from exc
    return jwt


def create_access_token(user_id: int, username: str) -> tuple[str, int]:
    """返回 (token, expires_in 秒)。"""
    jwt = _jwt()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=EXPIRE_HOURS)
    token = jwt.encode(
        {"sub": str(user_id), "username": username, "iat": now, "exp": exp},
        _secret(),
        algorithm=ALGORITHM,
    )
    return token, EXPIRE_HOURS * 3600


def decode_token(token: str) -> dict:
    jwt = _jwt()
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(UNAUTHORIZED, "登录已过期，请重新登录", 401) from exc
    except Exception as exc:  # noqa: BLE001 - 无效 token 一律 40101
        raise ApiError(UNAUTHORIZED, "令牌无效", 401) from exc


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("Authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip() or None
    return None


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """必须登录。缺 token / 无效 / 过期 / 用户已删 → 40101。"""
    token = _bearer(request)
    if not token:
        raise ApiError(UNAUTHORIZED, "请先登录", 401)
    payload = decode_token(token)
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise ApiError(UNAUTHORIZED, "令牌无效", 401) from exc
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(UNAUTHORIZED, "用户不存在", 401)
    return user


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """可选登录。带坏 token 也不报错（按匿名处理），避免匿名页面被过期 token 卡死。"""
    if not _bearer(request):
        return None
    try:
        return get_current_user(request, db)
    except ApiError:
        return None


def assert_same_user(session_user_id: int | None, user: User | None) -> None:
    """带 token 访问某 session 时校验归属；匿名访问（user 为 None）放行。"""
    if user is None:
        return
    if session_user_id is not None and session_user_id != user.user_id:
        raise ApiError(FORBIDDEN, "无权访问该会话", 403)


__all__ = [
    "EXPIRE_HOURS", "hash_password", "verify_password", "create_access_token",
    "decode_token", "get_current_user", "get_current_user_optional",
    "assert_same_user", "ensure_configured",
]
