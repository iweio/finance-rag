"""FastAPI 后端：10 个 HTTP 接口（8 个既有 + 2 个认证）。

字段名与 `契约/API.md` 逐字对应。改名即返工——前端 axios、Postman 集合全要跟着改。

启动：
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

两种运行模式：
    RAG_MODE=real  （默认）真检索，要 Milvus + 模型
    RAG_MODE=mock  假检索，只要 fixtures.json；/health 会带 "mode": "mock"

本轮（v1.1）修掉的，逐条对应《前后端补齐整改计划》：
    B1  chat 只发一个 done（rag_core 的 done 被拦掉，落库后自己发，带真 qa_id）
    B2  /meta/filters 改读 语料元数据.xlsx 出文档级字典（不再查空的 knowledge_point）
    B4  /history.created_at 是 14 位 YYYYMMDDHHMMSS
    B5  /favorite 的 items 补 doc_title / clause_no
    B6  /health 依赖不通时仍返回统一响应包 + 50301
    B7  参数校验失败 → 40001（不再裸 422）
    B8  chat 首问 upsert session（title=问句前 30 字）
    B15 建表失败不被静默吞，/health 暴露 mysql.schema
    B16 落库失败记日志（不再 except: pass）
    B20 /auth/register、/auth/login + JWT 依赖 + 收藏按 user_id 隔离
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# rag_core / backend 在项目根下（容器里是 /app）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.database as db_mod  # noqa: E402 - 需要读运行时更新的 SCHEMA_OK
from backend import auth  # noqa: E402
from backend.database import SessionLocal, get_db, init_db  # noqa: E402
from backend.errors import (  # noqa: E402
    ApiError, CLAUSE_NOT_FOUND, DB_ERROR, DEPENDENCY_DOWN, FORBIDDEN, GENERATE_FAILED,
    PARAM_INVALID, RETRIEVE_FAILED, SESSION_NOT_FOUND, UNAUTHORIZED, USERNAME_EXISTS,
    ok, register_handlers,
)
from backend.graph_service import GraphDataError, get_graph  # noqa: E402
from backend.meta_dict import get_filters  # noqa: E402
from backend.models import Favorite, QaHistory, Session_, User  # noqa: E402
from rag_core import Filters, RagEngine, RagError  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backend")

RAG_MODE = os.environ.get("RAG_MODE", "real").lower()
API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")
APP_ENV = os.environ.get("APP_ENV", "development").lower()
VERSION = "0.1.0"

rag_engine: Any = None  # RagEngine | MockRagEngine


# ---------------------------------------------------------------- 启动

def _build_engine() -> Any:
    if RAG_MODE == "mock":
        from rag_core.mock_engine import MockRagEngine

        logger.warning("RAG_MODE=mock：检索走 fixtures，不可用于交付演示")
        return MockRagEngine()
    return RagEngine()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global rag_engine
    auth.ensure_configured()  # 生产缺 JWT_SECRET 在这里就炸，别拖到第一次登录
    init_db()
    try:
        filters = get_filters()
        logger.info("筛选字典就绪：domain=%d org=%d topic_tags=%d",
                    len(filters["domain"]), len(filters["org"]), len(filters["topic_tags"]))
    except ApiError as exc:
        logger.error("筛选字典加载失败（/meta/filters 会返回 50003）：%s", exc.message)
    rag_engine = _build_engine()
    yield


app = FastAPI(title="金融制度知识库 RAG API", version=VERSION, lifespan=lifespan)
register_handlers(app)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if not _origins:
    _origins = ["*"] if APP_ENV != "production" else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # B12：`allow_origins=["*"]` 与 `allow_credentials=True` 不能同时开——
    # 浏览器会拒绝 `Access-Control-Allow-Origin: *` + credentials 的组合。
    # 本项目用 Bearer token、不用 Cookie，所以关掉 credentials 即可。
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 请求模型

DomainLiteral = Literal["银行", "证券", "保险", "外汇", "综合"]
StatusLiteral = Literal["有效", "尚未生效", "已废止"]


class FiltersIn(BaseModel):
    domain: DomainLiteral | None = None
    status: StatusLiteral | None = None
    org: str | None = Field(default=None, max_length=128)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(..., min_length=1, max_length=64)
    filters: FiltersIn | None = None


class SearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=100)
    page: int = Field(default=1, ge=1)
    size: int = Field(default=10, ge=1, le=50)
    filters: FiltersIn | None = None


class FavoriteCreate(BaseModel):
    clause_id: str | None = Field(default=None, max_length=64)
    qa_id: int | None = None


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_\u4e00-\u9fa5]+$")
    password: str = Field(..., min_length=6, max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------- 工具

def _to_filters(fi: FiltersIn | None) -> Filters | None:
    if fi is None:
        return None
    if not any((fi.domain, fi.status, fi.org)):
        return None
    return Filters(domain=fi.domain, status=fi.status, org=fi.org)


def _ts14(value: datetime | None) -> int:
    """DATETIME → 14 位 `YYYYMMDDHHMMSS`（B4）。契约 §6 明确这是时间戳不是日期。"""
    if value is None:
        return 0
    return int(value.strftime("%Y%m%d%H%M%S"))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _rag_http(e: RagError) -> ApiError:
    mapping = {
        "RETRIEVE_FAILED": (RETRIEVE_FAILED, 500),
        "GENERATE_FAILED": (GENERATE_FAILED, 500),
        "NOT_FOUND": (CLAUSE_NOT_FOUND, 404),
    }
    code, status = mapping.get(e.code, (RETRIEVE_FAILED, 500))
    return ApiError(code, e.message, status)


def _ensure_session(db: Session, session_id: str, title: str, user_id: int | None) -> None:
    """B8：首问 upsert。并发下可能撞唯一键，撞了就当已存在（幂等）。"""
    row = db.get(Session_, session_id)
    if row is None:
        try:
            db.add(Session_(session_id=session_id, user_id=user_id, title=title[:30]))
            db.commit()
        except IntegrityError:
            db.rollback()
        return
    # 之前是匿名会话、这次登录了 —— 把归属补上（不覆盖已有 user_id）
    if row.user_id is None and user_id is not None:
        row.user_id = user_id
        if not row.title:
            row.title = title[:30]
        db.commit()


def _session_user_id(db: Session, session_id: str) -> tuple[bool, int | None]:
    """返回 (会话是否存在, 归属 user_id)。"""
    row = db.get(Session_, session_id)
    if row is None:
        return False, None
    return True, row.user_id


def _fav_enrich(rows: list[Favorite]) -> list[dict]:
    """B5：批量补 doc_title / clause_no，避免 50 条收藏 = 50 次 Milvus 查询。"""
    ids = [r.clause_id for r in rows if r.clause_id]
    briefs: dict[str, Any] = {}
    if ids and rag_engine is not None:
        try:
            getter = getattr(rag_engine, "get_clauses", None)
            if getter is not None:
                for d in getter(ids):
                    briefs[d.clause_id] = d
            else:  # pragma: no cover - 兼容没有增补方法的引擎
                for cid in ids:
                    try:
                        briefs[cid] = rag_engine.get_clause(cid)
                    except RagError:
                        pass
        except Exception as exc:  # noqa: BLE001 - 补字段失败不该让收藏列表 500
            logger.warning("收藏条目补字段失败：%s", exc)

    out = []
    for r in rows:
        d = briefs.get(r.clause_id or "")
        out.append({
            "favorite_id": r.favorite_id,
            "clause_id": r.clause_id,
            "doc_title": getattr(d, "doc_title", None),
            "clause_no": getattr(d, "clause_no", None),
            "created_at": _ts14(r.created_at),
        })
    return out


# ---------------------------------------------------------------- 1. chat

@app.post(f"{API_PREFIX}/chat")
async def chat(
    req: ChatRequest,
    user: User | None = Depends(auth.get_current_user_optional),
):
    filters = _to_filters(req.filters)

    def event_stream():
        answer_parts: list[str] = []
        sources: list[dict] = []
        score = 0.0
        t0 = time.time()

        try:
            for event in rag_engine.answer_stream(req.question, filters, req.session_id):
                # B1：rag_core 也会发 done（qa_id=-1），这里必须拦掉，
                # 否则一个流里出现两个 done，前端在第一个就收尾、永远拿不到真 qa_id。
                if event.event == "done":
                    continue
                # error 是终止事件（契约 §1）。引擎可能用抛异常、也可能 yield 一个
                # error 事件来报失败 —— 只 catch 异常的话，后者会漏到这里继续往下走，
                # 于是同一个流里既有 error 又有 done，前端收到 done 就收尾、
                # 再也看不到错误态。
                if event.event == "error":
                    yield _sse("error", event.data)
                    return
                if event.event == "delta":
                    answer_parts.append(str(event.data.get("text", "")))
                elif event.event == "sources":
                    sources = event.data.get("sources") or []
                elif event.event == "score":
                    score = float(event.data.get("score") or 0.0)
                yield _sse(event.event, event.data)
        except RagError as exc:
            yield _sse("error", {"code": _rag_http(exc).code, "message": exc.message})
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat 流式生成异常")
            yield _sse("error", {"code": RETRIEVE_FAILED, "message": f"生成失败: {exc}"})
            return

        # 落库（B8 + B16）：失败要记日志，不能 except: pass
        qa_id = -1
        try:
            with SessionLocal() as db:
                _ensure_session(db, req.session_id, req.question, user.user_id if user else None)
                qa = QaHistory(
                    session_id=req.session_id,
                    question=req.question,
                    answer="".join(answer_parts),
                    sources=sources,
                    score=score,
                )
                db.add(qa)
                db.commit()
                db.refresh(qa)
                qa_id = int(qa.qa_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("qa_history 落库失败（答案已返回，qa_id=-1）：%s", exc)

        yield _sse("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 让 nginx 别缓冲（否则前端看不到逐句）
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------- 2. search

@app.post(f"{API_PREFIX}/search")
async def search(req: SearchRequest):
    try:
        result = rag_engine.search(req.keyword, _to_filters(req.filters), req.page, req.size)
    except RagError as exc:
        raise _rag_http(exc) from exc
    return ok({
        "total": result.total,
        "page": result.page,
        "size": result.size,
        "items": [
            {
                "clause_id": i.clause_id, "doc_title": i.doc_title, "clause_no": i.clause_no,
                "snippet": i.snippet, "highlight": i.highlight, "status": i.status,
                "source_url": i.source_url, "score": i.score,
            }
            for i in result.items
        ],
    })


# ---------------------------------------------------------------- 3/4. clause

@app.get(f"{API_PREFIX}/clause/{{clause_id}}")
async def get_clause(clause_id: str):
    try:
        d = rag_engine.get_clause(clause_id)
    except RagError as exc:
        raise _rag_http(exc) from exc
    return ok({
        "clause_id": d.clause_id, "doc_id": d.doc_id, "doc_title": d.doc_title,
        "clause_no": d.clause_no, "clause_index": d.clause_index, "chapter": d.chapter,
        "text": d.text, "doc_type": d.doc_type, "domain": d.domain, "org": d.org,
        "doc_number": d.doc_number, "publish_date": d.publish_date,
        "effective_date": d.effective_date, "expire_date": d.expire_date,
        "status": d.status, "source_url": d.source_url, "fetched_at": d.fetched_at,
    })


@app.get(f"{API_PREFIX}/clause/{{clause_id}}/related")
async def get_related_clauses(clause_id: str, top_k: int = Query(default=5, ge=1, le=20)):
    try:
        related = rag_engine.get_related_clauses(clause_id, top_k)
    except RagError as exc:
        raise _rag_http(exc) from exc
    return ok({
        "clause_id": clause_id,
        "related": [
            {
                "clause_id": r.clause_id, "doc_id": r.doc_id, "doc_title": r.doc_title,
                "clause_no": r.clause_no, "clause_index": r.clause_index, "chapter": r.chapter,
                "snippet": r.snippet, "score": r.score, "status": r.status,
                "effective_date": r.effective_date, "source_url": r.source_url,
                "relation_type": r.relation_type,
            }
            for r in related
        ],
    })


# ---------------------------------------------------------------- 5. meta/filters

@app.get(f"{API_PREFIX}/meta/filters")
async def meta_filters():
    # B2：文档级口径，源是 语料元数据.xlsx，不是 MySQL 的条文表
    return ok(get_filters())


# ---------------------------------------------------------------- 5b. 知识图谱

def _graph_error(exc: GraphDataError) -> ApiError:
    if exc.code == "NOT_FOUND":
        return ApiError(CLAUSE_NOT_FOUND, exc.message, 404)
    if exc.code == "INVALID":
        return ApiError(PARAM_INVALID, exc.message, 400)
    return ApiError(DEPENDENCY_DOWN, exc.message, 503)


def _parse_edge_types(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


@app.get(f"{API_PREFIX}/graph/stats")
async def graph_stats():
    try:
        return ok(get_graph().stats())
    except GraphDataError as exc:
        raise _graph_error(exc) from exc


@app.get(f"{API_PREFIX}/graph/neighbors")
async def graph_neighbors(
    clause_id: str = Query(..., min_length=1, max_length=64),
    hops: int = Query(default=1, ge=1, le=2),
    edge_types: str | None = Query(default=None, description="逗号分隔，如 CITES,NEXT"),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        return ok(get_graph().subgraph(clause_id, hops=hops, limit=limit,
                                       edge_types=_parse_edge_types(edge_types)))
    except GraphDataError as exc:
        raise _graph_error(exc) from exc


@app.get(f"{API_PREFIX}/graph/subgraph")
async def graph_subgraph(
    clause_id: str = Query(..., min_length=1, max_length=64),
    hops: int = Query(default=2, ge=1, le=2),
    edge_types: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=300),
):
    try:
        return ok(get_graph().subgraph(clause_id, hops=hops, limit=limit,
                                       edge_types=_parse_edge_types(edge_types)))
    except GraphDataError as exc:
        raise _graph_error(exc) from exc


# ---------------------------------------------------------------- 6. history

@app.get(f"{API_PREFIX}/history")
async def get_history(
    session_id: str = Query(..., min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User | None = Depends(auth.get_current_user_optional),
):
    exists, owner = _session_user_id(db, session_id)
    if not exists:
        raise ApiError(SESSION_NOT_FOUND, "会话不存在", 404)
    auth.assert_same_user(owner, user)

    try:
        total = db.query(sa_func.count(QaHistory.qa_id)).filter(
            QaHistory.session_id == session_id
        ).scalar() or 0
        items = (
            db.query(QaHistory)
            .filter(QaHistory.session_id == session_id)
            .order_by(QaHistory.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("history 查询失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc

    return ok({
        "total": int(total), "page": page, "size": size,
        "items": [
            {
                "qa_id": it.qa_id, "question": it.question, "answer": it.answer,
                "score": it.score, "created_at": _ts14(it.created_at),
                # 契约「精简引用」只要 3 个字段
                "sources": [
                    {"clause_id": s.get("clause_id"), "clause_no": s.get("clause_no"),
                     "doc_title": s.get("doc_title")}
                    for s in (it.sources or [])
                    if isinstance(s, dict)
                ],
            }
            for it in items
        ],
    })


@app.delete(f"{API_PREFIX}/history")
async def delete_history(
    session_id: str | None = Query(default=None, max_length=64),
    qa_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User | None = Depends(auth.get_current_user_optional),
):
    if (session_id is None) == (qa_id is None):
        raise ApiError(PARAM_INVALID, "session_id 与 qa_id 必须传且只传一个", 400)

    try:
        if session_id is not None:
            exists, owner = _session_user_id(db, session_id)
            if not exists:
                raise ApiError(SESSION_NOT_FOUND, "会话不存在", 404)
            auth.assert_same_user(owner, user)
            n = db.query(QaHistory).filter(QaHistory.session_id == session_id).delete()
        else:
            row = db.get(QaHistory, qa_id)
            if row is not None:
                _, owner = _session_user_id(db, row.session_id)
                auth.assert_same_user(owner, user)
            n = db.query(QaHistory).filter(QaHistory.qa_id == qa_id).delete()
        db.commit()
        return ok({"deleted": int(n)})
    except ApiError:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("history 删除失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc


# ---------------------------------------------------------------- 7. favorite

@app.get(f"{API_PREFIX}/favorite")
async def get_favorites(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    clause_id: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User | None = Depends(auth.get_current_user_optional),
):
    try:
        q = db.query(Favorite)
        q = q.filter(Favorite.user_id == (user.user_id if user else None))
        if clause_id:
            q = q.filter(Favorite.clause_id == clause_id)
        total = q.count()
        rows = q.order_by(Favorite.created_at.desc()).offset((page - 1) * size).limit(size).all()
    except Exception as exc:  # noqa: BLE001
        logger.exception("favorite 查询失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc

    return ok({"total": total, "page": page, "size": size, "items": _fav_enrich(rows)})


@app.post(f"{API_PREFIX}/favorite")
async def create_favorite(
    req: FavoriteCreate,
    db: Session = Depends(get_db),
    user: User | None = Depends(auth.get_current_user_optional),
):
    if (req.clause_id is None) == (req.qa_id is None):
        raise ApiError(PARAM_INVALID, "clause_id 与 qa_id 必须二选一", 400)

    uid = user.user_id if user else None
    try:
        q = db.query(Favorite).filter(Favorite.user_id == uid)
        if req.clause_id is not None:
            q = q.filter(Favorite.clause_id == req.clause_id)
        else:
            q = q.filter(Favorite.qa_id == req.qa_id)
        existing = q.first()
        if existing is not None:
            return ok({"favorite_id": existing.favorite_id})

        fav = Favorite(user_id=uid, clause_id=req.clause_id, qa_id=req.qa_id)
        db.add(fav)
        db.commit()
        db.refresh(fav)
        return ok({"favorite_id": fav.favorite_id})
    except IntegrityError:
        # 并发下两次同收藏：唯一键拦住了，回查返回同一条（幂等）
        db.rollback()
        q = db.query(Favorite).filter(Favorite.user_id == uid)
        q = (q.filter(Favorite.clause_id == req.clause_id) if req.clause_id is not None
             else q.filter(Favorite.qa_id == req.qa_id))
        row = q.first()
        if row is not None:
            return ok({"favorite_id": row.favorite_id})
        raise ApiError(DB_ERROR, "收藏写入失败", 500) from None
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("favorite 写入失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc


@app.delete(f"{API_PREFIX}/favorite")
async def delete_favorite(
    favorite_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User | None = Depends(auth.get_current_user_optional),
):
    try:
        row = db.get(Favorite, favorite_id)
        if row is not None and user is not None and row.user_id != user.user_id:
            raise ApiError(FORBIDDEN, "无权删除该收藏", 403)
        n = db.query(Favorite).filter(Favorite.favorite_id == favorite_id).delete()
        db.commit()
        return ok({"deleted": int(n)})
    except ApiError:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("favorite 删除失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc


# ---------------------------------------------------------------- 8. health

@app.get(f"{API_PREFIX}/health")
async def health():
    result: dict[str, Any] = {
        "service": "ok",
        "mysql": {"status": "down", "latency_ms": 0, "schema": "down"},
        "milvus": {"status": "down", "latency_ms": 0,
                   "collection": os.environ.get("MILVUS_COLLECTION", "finance_clause"),
                   "row_count": 0},
        "version": VERSION,
        "mode": RAG_MODE,
    }
    any_down = False

    t0 = time.time()
    try:
        with SessionLocal() as db:
            db.execute(sa_text("SELECT 1"))
        result["mysql"] = {
            "status": "ok",
            "latency_ms": int((time.time() - t0) * 1000),
            # B15：表建不起来时这里必须是 down，不能让接口逐个 500 而 health 报 ok
            "schema": "ok" if db_mod.SCHEMA_OK else "down",
        }
        if not db_mod.SCHEMA_OK:
            any_down = True
    except Exception as exc:  # noqa: BLE001
        result["mysql"] = {"status": "down", "latency_ms": 0, "schema": "down",
                           "error": str(exc)[:200]}
        any_down = True

    t0 = time.time()
    if RAG_MODE == "mock":
        snap = rag_engine.health_snapshot()
        result["milvus"] = {**snap.get("milvus", {}), "mode": "mock"}
        result["mode"] = "mock"
    else:
        try:
            from pymilvus import Collection, connections

            host = os.environ.get("MILVUS_HOST", "localhost")
            port = int(os.environ.get("MILVUS_PORT", "19530"))
            alias = "health_probe"
            connections.connect(alias=alias, host=host, port=port, timeout=3)
            coll = Collection(result["milvus"]["collection"], using=alias)
            coll.load()
            n = coll.query(expr="clause_id != ''", output_fields=["count(*)"])[0]["count(*)"]
            result["milvus"] = {
                "status": "ok",
                "latency_ms": int((time.time() - t0) * 1000),
                "collection": result["milvus"]["collection"],
                "row_count": int(n),
            }
            connections.disconnect(alias)
        except Exception as exc:  # noqa: BLE001
            result["milvus"] = {**result["milvus"], "status": "down",
                                "error": str(exc)[:200]}
            any_down = True

    if any_down:
        # B6：503 也要是统一响应包，不能裸返回 result
        return JSONResponse(
            {"code": DEPENDENCY_DOWN, "message": "依赖不可用", "data": result},
            status_code=503,
        )
    return ok(result)


# ---------------------------------------------------------------- 9/10. auth

@app.post(f"{API_PREFIX}/auth/register")
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    username = req.username.strip()
    try:
        if db.query(User).filter(User.username == username).first() is not None:
            raise ApiError(USERNAME_EXISTS, "用户名已存在", 409)
        user = User(username=username, password=auth.hash_password(req.password))
        db.add(user)
        db.commit()
        db.refresh(user)
        return ok({"user_id": user.user_id, "username": user.username})
    except ApiError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise ApiError(USERNAME_EXISTS, "用户名已存在", 409) from None
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("注册失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc


@app.post(f"{API_PREFIX}/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter(User.username == req.username.strip()).first()
    except Exception as exc:  # noqa: BLE001
        logger.exception("登录查询失败")
        raise ApiError(DB_ERROR, f"数据库错误: {exc}", 500) from exc
    if user is None or not auth.verify_password(req.password, user.password):
        # 不区分「用户不存在」与「密码错」，避免撞库探测
        raise ApiError(UNAUTHORIZED, "用户名或密码错误", 401)

    token, expires_in = auth.create_access_token(user.user_id, user.username)
    return ok({"token": token, "user_id": user.user_id,
               "username": user.username, "expires_in": expires_in})
