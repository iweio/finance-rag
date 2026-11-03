"""FastAPI 后端：8 个 HTTP 接口。

字段名与 契约/API.md 逐字对应。改名即返工——前端 axios、Postman 集合全要跟着改。

启动：
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text, func as sa_func
from sqlalchemy.orm import Session

# rag_core 在上级目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_core import RagEngine, Filters, RagError
from backend.database import engine, get_db, SessionLocal, Base
from backend.models import QaHistory, Session_, Favorite, KnowledgePoint

# ---------- 启动 ----------

rag_engine: RagEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_engine
    rag_engine = RagEngine()
    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

    import threading
    def _preload():
        try:
            rag_engine._retriever._load_embed_model()
            rag_engine._retriever._build_bm25()
            print("[preload] embed model + BM25 index ready")
        except Exception as e:
            print(f"[preload] warning: {e}")
    threading.Thread(target=_preload, daemon=True).start()

    yield


app = FastAPI(
    title="金融制度知识库 RAG API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")

# ---------- 请求/响应模型 ----------


class FiltersIn(BaseModel):
    domain: str | None = None
    status: str | None = None
    org: str | None = None


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(..., min_length=1)
    filters: FiltersIn | None = None


class SearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=100)
    page: int = Field(default=1, ge=1)
    size: int = Field(default=10, ge=1, le=50)
    filters: FiltersIn | None = None


class FavoriteCreate(BaseModel):
    clause_id: str | None = None
    qa_id: int | None = None


# ---------- 统一响应包 ----------


def ok(data: Any = None) -> JSONResponse:
    return JSONResponse({"code": 0, "message": "ok", "data": data})


def err(code: int, message: str, http_status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "data": None},
        status_code=http_status,
    )


def _to_filters(fi: FiltersIn | None) -> Filters | None:
    if fi is None:
        return None
    return Filters(domain=fi.domain, status=fi.status, org=fi.org)


# ---------- 1. POST /api/v1/chat ----------


@app.post(f"{API_PREFIX}/chat")
async def chat(req: ChatRequest):
    try:
        filters = _to_filters(req.filters)

        def event_stream():
            qa_id = -1
            answer_text = []
            sources_data = []
            score_val = 0.0
            try:
                for event in rag_engine.answer_stream(
                    req.question, filters, req.session_id,
                ):
                    if event.event == "delta":
                        answer_text.append(event.data.get("text", ""))
                    elif event.event == "sources":
                        sources_data = event.data.get("sources", [])
                    elif event.event == "score":
                        score_val = event.data.get("score", 0.0)

                    yield f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"

                try:
                    with SessionLocal() as db:
                        qa = QaHistory(
                            session_id=req.session_id,
                            question=req.question,
                            answer="".join(answer_text),
                            sources=sources_data,
                            score=score_val,
                        )
                        db.add(qa)
                        db.commit()
                        db.refresh(qa)
                        qa_id = qa.qa_id
                except Exception:
                    pass

                yield f"event: done\ndata: {json.dumps({'qa_id': qa_id, 'elapsed_ms': 0})}\n\n"

            except RagError as e:
                code_map = {
                    "RETRIEVE_FAILED": 50001,
                    "GENERATE_FAILED": 50002,
                    "NOT_FOUND": 40401,
                }
                yield f"event: error\ndata: {json.dumps({'code': code_map.get(e.code, 50001), 'message': e.message}, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except RagError as e:
        code_map = {
            "RETRIEVE_FAILED": (50001, 500),
            "GENERATE_FAILED": (50002, 500),
            "NOT_FOUND": (40401, 404),
        }
        api_code, http_code = code_map.get(e.code, (50001, 500))
        return err(api_code, e.message, http_code)


# ---------- 2. POST /api/v1/search ----------


@app.post(f"{API_PREFIX}/search")
async def search(req: SearchRequest):
    try:
        filters = _to_filters(req.filters)
        result = rag_engine.search(req.keyword, filters, req.page, req.size)
        return ok({
            "total": result.total,
            "page": result.page,
            "size": result.size,
            "items": [
                {
                    "clause_id": item.clause_id,
                    "doc_title": item.doc_title,
                    "clause_no": item.clause_no,
                    "snippet": item.snippet,
                    "highlight": item.highlight,
                    "status": item.status,
                    "source_url": item.source_url,
                    "score": item.score,
                }
                for item in result.items
            ],
        })
    except RagError as e:
        code_map = {
            "RETRIEVE_FAILED": (50001, 500),
            "NOT_FOUND": (40401, 404),
        }
        api_code, http_code = code_map.get(e.code, (50001, 500))
        return err(api_code, e.message, http_code)


# ---------- 3. GET /api/v1/clause/{clause_id} ----------


@app.get(f"{API_PREFIX}/clause/{{clause_id}}")
async def get_clause(clause_id: str):
    try:
        detail = rag_engine.get_clause(clause_id)
        return ok({
            "clause_id": detail.clause_id,
            "doc_id": detail.doc_id,
            "doc_title": detail.doc_title,
            "clause_no": detail.clause_no,
            "clause_index": detail.clause_index,
            "chapter": detail.chapter,
            "text": detail.text,
            "doc_type": detail.doc_type,
            "domain": detail.domain,
            "org": detail.org,
            "doc_number": detail.doc_number,
            "publish_date": detail.publish_date,
            "effective_date": detail.effective_date,
            "expire_date": detail.expire_date,
            "status": detail.status,
            "source_url": detail.source_url,
            "fetched_at": detail.fetched_at,
        })
    except RagError as e:
        if e.code == "NOT_FOUND":
            return err(40401, e.message, 404)
        return err(50001, e.message, 500)


# ---------- 4. GET /api/v1/clause/{clause_id}/related ----------


@app.get(f"{API_PREFIX}/clause/{{clause_id}}/related")
async def get_related_clauses(
    clause_id: str,
    top_k: int = Query(default=5, ge=1, le=20),
):
    try:
        related = rag_engine.get_related_clauses(clause_id, top_k)
        return ok({
            "clause_id": clause_id,
            "related": [
                {
                    "clause_id": r.clause_id,
                    "doc_id": r.doc_id,
                    "doc_title": r.doc_title,
                    "clause_no": r.clause_no,
                    "clause_index": r.clause_index,
                    "chapter": r.chapter,
                    "snippet": r.snippet,
                    "score": r.score,
                    "status": r.status,
                    "effective_date": r.effective_date,
                    "source_url": r.source_url,
                    "relation_type": r.relation_type,
                }
                for r in related
            ],
        })
    except RagError as e:
        if e.code == "NOT_FOUND":
            return err(40401, e.message, 404)
        return err(50001, e.message, 500)


# ---------- 5. GET /api/v1/meta/filters ----------


@app.get(f"{API_PREFIX}/meta/filters")
async def meta_filters(db: Session = Depends(get_db)):
    try:
        domain_rows = db.execute(
            sa_text("SELECT domain, COUNT(*) as cnt FROM knowledge_point GROUP BY domain ORDER BY cnt DESC")
        ).fetchall()
        status_rows = db.execute(
            sa_text("SELECT status, COUNT(*) as cnt FROM knowledge_point GROUP BY status ORDER BY cnt DESC")
        ).fetchall()
        doc_type_rows = db.execute(
            sa_text("SELECT doc_type, COUNT(*) as cnt FROM knowledge_point GROUP BY doc_type ORDER BY cnt DESC")
        ).fetchall()

        org_rows = db.execute(
            sa_text("SELECT org, COUNT(*) as cnt FROM knowledge_point GROUP BY org ORDER BY cnt DESC")
        ).fetchall()

        date_range = db.execute(
            sa_text("SELECT MIN(effective_date) as mn, MAX(effective_date) as mx FROM knowledge_point WHERE effective_date IS NOT NULL")
        ).fetchone()

        return ok({
            "domain": [{"value": r[0], "label": r[0], "count": r[1]} for r in domain_rows],
            "status": [{"value": r[0], "label": r[0], "count": r[1]} for r in status_rows],
            "org": [{"value": r[0], "label": r[0], "count": r[1]} for r in org_rows],
            "doc_type": [{"value": r[0], "label": r[0], "count": r[1]} for r in doc_type_rows],
            "topic_tags": [],
            "date_range": {
                "min": date_range[0] if date_range else None,
                "max": date_range[1] if date_range else None,
            },
        })
    except Exception as e:
        return err(50003, f"数据库错误: {e}", 500)


# ---------- 6. /api/v1/history ----------


@app.get(f"{API_PREFIX}/history")
async def get_history(
    session_id: str = Query(...),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    try:
        total = db.query(sa_func.count(QaHistory.qa_id)).filter(
            QaHistory.session_id == session_id
        ).scalar() or 0

        items = db.query(QaHistory).filter(
            QaHistory.session_id == session_id
        ).order_by(QaHistory.created_at.desc()).offset(
            (page - 1) * size
        ).limit(size).all()

        return ok({
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "qa_id": item.qa_id,
                    "question": item.question,
                    "answer": item.answer,
                    "score": item.score,
                    "created_at": int(item.created_at.timestamp() * 1000) if item.created_at else 0,
                    "sources": item.sources or [],
                }
                for item in items
            ],
        })
    except Exception as e:
        return err(50003, f"数据库错误: {e}", 500)


@app.delete(f"{API_PREFIX}/history")
async def delete_history(
    session_id: str | None = Query(default=None),
    qa_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if (session_id is None) == (qa_id is None):
        return err(40001, "session_id 与 qa_id 必须传且只传一个", 400)

    try:
        if session_id is not None:
            n = db.query(QaHistory).filter(
                QaHistory.session_id == session_id
            ).delete()
        else:
            n = db.query(QaHistory).filter(
                QaHistory.qa_id == qa_id
            ).delete()
        db.commit()
        return ok({"deleted": n})
    except Exception as e:
        db.rollback()
        return err(50003, f"数据库错误: {e}", 500)


# ---------- 7. /api/v1/favorite ----------


@app.get(f"{API_PREFIX}/favorite")
async def get_favorites(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=50),
    clause_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        q = db.query(Favorite)
        if clause_id:
            q = q.filter(Favorite.clause_id == clause_id)

        total = q.count()
        items = q.order_by(Favorite.created_at.desc()).offset(
            (page - 1) * size
        ).limit(size).all()

        return ok({
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "favorite_id": item.favorite_id,
                    "clause_id": item.clause_id,
                    "qa_id": item.qa_id,
                    "created_at": int(item.created_at.timestamp() * 1000) if item.created_at else 0,
                }
                for item in items
            ],
        })
    except Exception as e:
        return err(50003, f"数据库错误: {e}", 500)


@app.post(f"{API_PREFIX}/favorite")
async def create_favorite(req: FavoriteCreate, db: Session = Depends(get_db)):
    if req.clause_id is None and req.qa_id is None:
        return err(40001, "clause_id 与 qa_id 至少给一个", 400)

    try:
        existing = db.query(Favorite).filter(
            (Favorite.clause_id == req.clause_id) | (Favorite.qa_id == req.qa_id)
        ).first()
        if existing:
            return ok({"favorite_id": existing.favorite_id})

        fav = Favorite(clause_id=req.clause_id, qa_id=req.qa_id)
        db.add(fav)
        db.commit()
        db.refresh(fav)
        return ok({"favorite_id": fav.favorite_id})
    except Exception as e:
        db.rollback()
        return err(50003, f"数据库错误: {e}", 500)


@app.delete(f"{API_PREFIX}/favorite")
async def delete_favorite(
    favorite_id: int = Query(...),
    db: Session = Depends(get_db),
):
    try:
        n = db.query(Favorite).filter(Favorite.favorite_id == favorite_id).delete()
        db.commit()
        return ok({"deleted": n})
    except Exception as e:
        db.rollback()
        return err(50003, f"数据库错误: {e}", 500)


# ---------- 8. GET /api/v1/health ----------


@app.get(f"{API_PREFIX}/health")
async def health():
    result = {
        "service": "ok",
        "mysql": {"status": "down", "latency_ms": 0},
        "milvus": {"status": "down", "latency_ms": 0, "collection": "finance_clause", "row_count": 0},
        "version": "0.1.0",
    }
    any_down = False

    t0 = time.time()
    try:
        with SessionLocal() as db:
            db.execute(sa_text("SELECT 1"))
        result["mysql"] = {
            "status": "ok",
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        result["mysql"] = {"status": "down", "latency_ms": 0, "error": str(e)}
        any_down = True

    t0 = time.time()
    try:
        from pymilvus import Collection, connections
        connections.connect(alias="health", host=os.environ.get("MILVUS_HOST", "localhost"),
                            port=int(os.environ.get("MILVUS_PORT", "19530")))
        coll = Collection(os.environ.get("MILVUS_COLLECTION", "finance_clause"), using="health")
        coll.load()
        n = coll.query(expr="clause_id != ''", output_fields=["count(*)"])[0]["count(*)"]
        result["milvus"] = {
            "status": "ok",
            "latency_ms": int((time.time() - t0) * 1000),
            "collection": "finance_clause",
            "row_count": n,
        }
        connections.disconnect("health")
    except Exception as e:
        result["milvus"] = {"status": "down", "latency_ms": 0, "error": str(e),
                            "collection": "finance_clause", "row_count": 0}
        any_down = True

    if any_down:
        return JSONResponse(result, status_code=503)
    return ok(result)
