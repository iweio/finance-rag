"""混合检索：向量 + BM25 + RRF 融合 + rerank。

向量检索走 Milvus（语义匹配），BM25 走本地索引（精确匹配：文号、专有名词、法规简称）。
RRF（Reciprocal Rank Fusion）按排名倒数加权融合两个列表，无需调权重。
rerank 用 bge-reranker 对融合后的 top-N 精排到 top-k。

三条不可违反（契约/MODULE.md §8）：
1. query 必须加 QUERY_PREFIX，passage 不加。
2. normalize_embeddings=True，与 Milvus COSINE 配对。
3. search_params 必须显式写 ef=256（理由是可复现，不是更准）。
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any

import numpy as np

from rag_core.errors import RagError
from rag_core.models import Filters, ClauseBrief

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
QUERY_PREFIX = os.environ.get("QUERY_PREFIX", "为这个句子生成表示以用于检索相关文章：")

MILVUS_HOST = os.environ.get("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.environ.get("MILVUS_PORT", "19530"))
MILVUS_COLLECTION = os.environ.get("MILVUS_COLLECTION", "finance_clause")

SEARCH_EF = 256
RERANK_TOP_N = 50
RERANK_FINAL_K = 10
RRF_K = 60

OUTPUT_FIELDS = [
    "clause_id", "doc_id", "doc_title", "clause_no", "chapter",
    "text", "status", "effective_date", "source_url", "clause_index",
    "org", "domain", "doc_type", "topic_tags",
]


class Retriever:
    """混合检索引擎。"""

    def __init__(self) -> None:
        self._milvus = None
        self._collection = None
        self._embed_model = None
        self._bm25 = None
        self._bm25_ids: list[str] = []
        self._bm25_texts: list[str] = []
        self._bm25_meta: list[dict] = []
        self._reranker = None

    def _connect_milvus(self) -> None:
        if self._collection is not None:
            return
        try:
            from pymilvus import Collection, connections
            connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()
        except Exception as e:
            raise RagError("RETRIEVE_FAILED", f"Milvus 连接失败: {e}") from e

    def _load_embed_model(self) -> None:
        if self._embed_model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RagError("RETRIEVE_FAILED",
                           "缺 sentence-transformers。pip install sentence-transformers")
        self._embed_model = SentenceTransformer(EMBED_MODEL, device="cpu")

    def _load_reranker(self) -> None:
        if self._reranker is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise RagError("RETRIEVE_FAILED",
                           "缺 sentence-transformers。pip install sentence-transformers")
        model_name = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
        self._reranker = CrossEncoder(model_name, device="cpu")

    def _build_bm25(self) -> None:
        if self._bm25 is not None:
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            raise RagError("RETRIEVE_FAILED",
                           "缺 rank_bm25。pip install rank-bm25")

        import jieba
        chunks_dir = os.path.join(ROOT, "产出", "chunks")
        if not os.path.isdir(chunks_dir):
            raise RagError("RETRIEVE_FAILED", "产出/chunks 不存在，先跑 pipeline.py --all")

        texts: list[str] = []
        ids: list[str] = []
        meta: list[dict] = []
        for fp in sorted(os.listdir(chunks_dir)):
            if not fp.endswith(".jsonl"):
                continue
            with open(os.path.join(chunks_dir, fp), encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    texts.append(row.get("embed_text", "") or row.get("text", ""))
                    ids.append(row["clause_id"])
                    meta.append({
                        "doc_id": row.get("doc_id", ""),
                        "doc_title": row.get("doc_title", ""),
                        "clause_no": row.get("clause_no", ""),
                        "clause_index": row.get("clause_index", 0),
                        "chapter": row.get("chapter"),
                        "text": row.get("text", ""),
                        "is_short": row.get("is_short", False),
                    })

        tokenized = [list(jieba.cut(t)) for t in texts]
        self._bm25 = BM25Okapi(tokenized)
        self._bm25_ids = ids
        self._bm25_texts = texts
        self._bm25_meta = meta

    def _encode_query(self, question: str) -> np.ndarray:
        self._load_embed_model()
        vec = self._embed_model.encode(
            QUERY_PREFIX + question,
            normalize_embeddings=True,
        )
        return vec.astype(np.float32).reshape(1, -1)

    def _vector_search(
        self, query_vec: np.ndarray, expr: str | None, top_k: int,
    ) -> list[dict]:
        self._connect_milvus()
        try:
            results = self._collection.search(
                data=query_vec,
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": SEARCH_EF}},
                limit=top_k,
                expr=expr,
                output_fields=OUTPUT_FIELDS,
            )
        except Exception as e:
            raise RagError("RETRIEVE_FAILED", f"Milvus 查询失败: {e}") from e

        hits = []
        for hit_list in results:
            for hit in hit_list:
                row = dict(hit.entity)
                row["score"] = hit.score
                hits.append(row)
        return hits

    def _bm25_search(
        self, keyword: str, expr_filter: str | None, top_k: int,
    ) -> list[dict]:
        self._build_bm25()
        import jieba
        tokens = list(jieba.cut(keyword))
        scores = self._bm25.get_scores(tokens)

        filter_set = None
        if expr_filter:
            filter_set = self._apply_filter(expr_filter)

        indexed = [(i, scores[i]) for i in range(len(scores))]
        if filter_set is not None:
            indexed = [(i, s) for i, s in indexed if i in filter_set]
        indexed.sort(key=lambda x: -x[1])

        hits = []
        for i, score in indexed[:top_k]:
            m = self._bm25_meta[i]
            hits.append({
                "clause_id": self._bm25_ids[i],
                "doc_id": m["doc_id"],
                "doc_title": m["doc_title"],
                "clause_no": m["clause_no"],
                "clause_index": m["clause_index"],
                "chapter": m["chapter"],
                "text": m["text"],
                "score": float(score),
                "status": "",
                "effective_date": None,
                "source_url": None,
                "org": "",
                "domain": "",
                "doc_type": "",
            })
        return hits

    def _apply_filter(self, expr: str | None) -> set[int] | None:
        if not expr:
            return None
        self._connect_milvus()
        try:
            results = self._collection.query(
                expr=expr,
                output_fields=["clause_id"],
            )
            return {r["clause_id"] for r in results}
        except Exception:
            return None

    def _rrf_fusion(
        self, vec_hits: list[dict], bm25_hits: list[dict], top_k: int,
    ) -> list[dict]:
        score_map: dict[str, float] = {}
        hit_map: dict[str, dict] = {}

        for rank, hit in enumerate(vec_hits):
            cid = hit["clause_id"]
            score_map[cid] = score_map.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            hit_map[cid] = hit

        for rank, hit in enumerate(bm25_hits):
            cid = hit["clause_id"]
            score_map[cid] = score_map.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            if cid not in hit_map:
                hit_map[cid] = hit

        sorted_ids = sorted(score_map, key=lambda x: -score_map[x])
        fused = []
        for cid in sorted_ids[:top_k]:
            h = dict(hit_map[cid])
            h["score"] = score_map[cid]
            fused.append(h)
        return fused

    def _rerank(self, question: str, hits: list[dict], top_k: int) -> list[dict]:
        if not hits:
            return []
        try:
            self._load_reranker()
        except RagError:
            return hits[:top_k]
        pairs = [(question, h.get("text", "")) for h in hits]
        try:
            scores = self._reranker.predict(pairs, show_progress_bar=False)
        except Exception:
            return hits[:top_k]

        for h, s in zip(hits, scores):
            h["score"] = float(s)
        hits.sort(key=lambda x: -x["score"])
        return hits[:top_k]

    @staticmethod
    def _hit_to_brief(hit: dict) -> ClauseBrief:
        text = hit.get("text", "")
        snippet = text[:200] + "..." if len(text) > 200 else text
        return ClauseBrief(
            clause_id=hit["clause_id"],
            doc_id=hit.get("doc_id", ""),
            doc_title=hit.get("doc_title", ""),
            clause_no=hit.get("clause_no", ""),
            clause_index=hit.get("clause_index", 0),
            chapter=hit.get("chapter"),
            snippet=snippet,
            score=hit.get("score", 0.0),
            status=hit.get("status", ""),
            effective_date=hit.get("effective_date"),
            source_url=hit.get("source_url"),
        )

    @staticmethod
    def _make_highlight(text: str, keyword: str) -> str:
        escaped_text = html.escape(text)
        escaped_kw = html.escape(keyword)
        pattern = re.compile(re.escape(escaped_kw), re.IGNORECASE)
        return pattern.sub(f"<em>{escaped_kw}</em>", escaped_text)

    def retrieve(
        self, question: str, filters: Filters | None, top_k: int = 10,
    ) -> list[ClauseBrief]:
        expr = filters.to_milvus_expr() if filters else None
        query_vec = self._encode_query(question)

        fetch_k = max(top_k * 3, RERANK_TOP_N)
        vec_hits = self._vector_search(query_vec, expr, fetch_k)
        fused = self._rrf_fusion(vec_hits, [], fetch_k)
        reranked = self._rerank(question, fused, top_k)
        return [self._hit_to_brief(h) for h in reranked]

    def search(
        self, keyword: str, filters: Filters | None, page: int, size: int,
    ) -> tuple[int, list[dict]]:
        from rag_core.models import SearchItem
        expr = filters.to_milvus_expr() if filters else None
        query_vec = self._encode_query(keyword)

        fetch_k = max(size * 5, RERANK_TOP_N)
        vec_hits = self._vector_search(query_vec, expr, fetch_k)
        bm25_hits = self._bm25_search(keyword, expr, fetch_k)
        fused = self._rrf_fusion(vec_hits, bm25_hits, fetch_k)
        reranked = self._rerank(keyword, fused, fetch_k)

        total = len(reranked)
        start = (page - 1) * size
        page_hits = reranked[start: start + size]

        items = []
        for h in page_hits:
            text = h.get("text", "")
            snippet = text[:200] + "..." if len(text) > 200 else text
            items.append(SearchItem(
                clause_id=h["clause_id"],
                doc_title=h.get("doc_title", ""),
                clause_no=h.get("clause_no", ""),
                snippet=snippet,
                highlight=self._make_highlight(snippet, keyword),
                status=h.get("status", ""),
                source_url=h.get("source_url"),
                score=h.get("score", 0.0),
            ))
        return total, items

    def get_clause(self, clause_id: str) -> dict:
        self._connect_milvus()
        try:
            results = self._collection.query(
                expr=f'clause_id == "{clause_id}"',
                output_fields=OUTPUT_FIELDS,
            )
        except Exception as e:
            raise RagError("RETRIEVE_FAILED", f"Milvus 查询失败: {e}") from e
        if not results:
            raise RagError("NOT_FOUND", f"条文 {clause_id} 不存在")
        return results[0]

    def get_related(
        self, clause_id: str, top_k: int = 5,
    ) -> list[tuple[dict, str]]:
        self._connect_milvus()
        try:
            results = self._collection.query(
                expr=f'clause_id == "{clause_id}"',
                output_fields=["doc_id", "chapter", "clause_index", "text"],
            )
        except Exception as e:
            raise RagError("RETRIEVE_FAILED", f"Milvus 查询失败: {e}") from e
        if not results:
            raise RagError("NOT_FOUND", f"条文 {clause_id} 不存在")

        src = results[0]
        doc_id = src["doc_id"]
        chapter = src.get("chapter", "")
        clause_index = src.get("clause_index", 0)
        src_text = src.get("text", "")

        related: list[tuple[dict, str]] = []

        parts = []
        if chapter:
            parts.append(f'doc_id == "{doc_id}"')
            parts.append(f'chapter == "{chapter}"')
            parts.append(f'clause_id != "{clause_id}"')
            expr = " and ".join(parts)
            try:
                siblings = self._collection.query(
                    expr=expr, output_fields=OUTPUT_FIELDS,
                )
            except Exception:
                siblings = []
            for s in siblings[:3]:
                related.append((s, "同章"))

        base_expr = f'doc_id == "{doc_id}" and clause_id != "{clause_id}"'
        try:
            same_doc = self._collection.query(
                expr=base_expr, output_fields=OUTPUT_FIELDS,
            )
        except Exception:
            same_doc = []
        existing_ids = {r["clause_id"] for r, _ in related}
        for s in same_doc:
            if s["clause_id"] not in existing_ids and len(related) < top_k:
                related.append((s, "同条" if s.get("clause_no", "").startswith(
                    src.get("clause_no", "")[:3]) else "相似"))
                existing_ids.add(s["clause_id"])

        if len(related) < top_k:
            query_vec = self._encode_query(src_text)
            vec_hits = self._vector_search(
                query_vec, f'clause_id != "{clause_id}"', top_k * 2,
            )
            for h in vec_hits:
                if h["clause_id"] not in existing_ids and len(related) < top_k:
                    related.append((h, "相似"))
                    existing_ids.add(h["clause_id"])

        return related[:top_k]
