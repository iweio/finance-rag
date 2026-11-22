"""统一响应包与错误码（契约/API.md §0.3 / §0.4）。

所有业务失败都走 `ApiError`，由这里的 handler 转成 `{code, message, data: null}`。
v1 的问题是没有全局 handler：参数校验失败时 FastAPI 直接返回裸 422，
前端拿到的是 `{"detail":[...]}` 而不是契约里的 `40001`，两套错误形状并存。

错误码表（与 API.md §0.4 一致，后四个是认证与兜底新增的，已写进契约）：
    40001 参数校验失败        400 资源不存在
    40101 未认证              40301 无权访问
    40401 条文不存在          40402 会话不存在
    40901 用户名已存在        50001 检索失败
    50002 生成失败            50003 数据库错误
    50000 服务器内部错误      50301 依赖不可用
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# ---- 码值常量：路由里不要写字面量，写错一个数字前端就分不清 ----
PARAM_INVALID = 40001
RESOURCE_NOT_FOUND = 40400
UNAUTHORIZED = 40101
FORBIDDEN = 40301
CLAUSE_NOT_FOUND = 40401
SESSION_NOT_FOUND = 40402
USERNAME_EXISTS = 40901
INTERNAL_ERROR = 50000
RETRIEVE_FAILED = 50001
GENERATE_FAILED = 50002
DB_ERROR = 50003
DEPENDENCY_DOWN = 50301


class ApiError(Exception):
    """业务异常。`http_status` 与 `code` 分开给，是因为 40101 既可能配 401 也可能配 400。"""

    def __init__(self, code: int, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


def ok(data: Any = None) -> JSONResponse:
    return JSONResponse({"code": 0, "message": "ok", "data": data})


def fail(code: int, message: str, http_status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "data": None}, status_code=http_status
    )


async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return fail(exc.code, exc.message, exc.http_status)


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 把 pydantic 的 loc/msg 压成一句人话；不把内部结构透给前端
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        parts.append(f"{loc}: {err.get('msg', '')}".strip(": "))
    return fail(PARAM_INVALID, "参数校验失败" + (f"（{'; '.join(parts)}）" if parts else ""), 400)


async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 未知路由 / 方法不允许等；契约里没有对应码，用 40400 兜底并写进 API.md
    if exc.status_code == 404:
        return fail(RESOURCE_NOT_FOUND, "资源不存在", 404)
    if exc.status_code in (401, 403):
        return fail(UNAUTHORIZED if exc.status_code == 401 else FORBIDDEN,
                    str(exc.detail) if exc.detail else "未认证", exc.status_code)
    return fail(INTERNAL_ERROR, str(exc.detail) if exc.detail else "请求失败", exc.status_code)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # 堆栈只进日志，绝不透给前端（契约/MODULE.md §5）
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return fail(INTERNAL_ERROR, "服务器内部错误", 500)


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
