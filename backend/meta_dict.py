"""筛选字典：把 `语料元数据.xlsx` 的文档级统计包成 `契约/API.md §5` 的 data 形状（B2）。

为什么不再查 MySQL `knowledge_point`：
- 那张表是**条文级**（8156 行），`count` 会是 8156 量级；而 API.md §5 的示例是**文档级**
  （合计 159，`status` 155/3/1）。口径不同，前端筛选项旁的数字会全错。
- 实测 xlsx 的文档级统计与 API.md 示例**逐项相等**，所以 xlsx 才是这份字典的准据。

三个容易做错的点：
1. `topic_tags` 是多选，各项之和 **206 > 159**，这是正常的，别拿它当总数。
2. `org` 要按顿号拆开（兼容半角逗号），否则联合发文在筛选器里筛不出来。
3. xlsx 缺失要报 `50003` 而不是返回空字典 —— 空字典会让前端筛选器整块消失，且不报错。
"""
from __future__ import annotations

import logging
import threading

from backend.corpus_meta import (
    CorpusMetaError, current_mtime, doc_level_counts, load_meta_by_doc,
)
from backend.errors import DB_ERROR, ApiError

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: dict = {"mtime": None, "filters": None}


def get_filters() -> dict:
    """契约/API.md §5 的 data 形状。按 xlsx 的 mtime 失效。"""
    try:
        mtime = current_mtime()
    except CorpusMetaError as exc:
        raise ApiError(
            DB_ERROR,
            f"{exc}（检查挂载/OUTPUT_DIR；不要用空字典兜底）",
            500,
        ) from exc

    with _LOCK:
        if _CACHE["mtime"] == mtime and _CACHE["filters"] is not None:
            return _CACHE["filters"]

    try:
        by_doc = load_meta_by_doc()
    except CorpusMetaError as exc:
        raise ApiError(
            DB_ERROR,
            f"{exc}（检查挂载/OUTPUT_DIR；不要用空字典兜底）",
            500,
        ) from exc

    effective = [
        m.get("effective_date")
        for m in by_doc.values()
        if isinstance(m.get("effective_date"), int)
    ]
    data = {
        "domain": doc_level_counts(by_doc, "domain", multi=False),
        "status": doc_level_counts(by_doc, "status", multi=False),
        "org": doc_level_counts(by_doc, "org", multi=True),
        "doc_type": doc_level_counts(by_doc, "doc_type", multi=False),
        "topic_tags": doc_level_counts(by_doc, "topic_tags", multi=True),
        "date_range": {
            "min": min(effective) if effective else None,
            "max": max(effective) if effective else None,
        },
    }
    with _LOCK:
        _CACHE.update(mtime=mtime, filters=data)
    return data


def self_check() -> dict:
    """离线自检：把口径汇总成一行，方便和 API.md §5 的示例数字对。"""
    data = get_filters()
    return {
        "domain_sum": sum(x["count"] for x in data["domain"]),
        "status_sum": sum(x["count"] for x in data["status"]),
        "doc_type_sum": sum(x["count"] for x in data["doc_type"]),
        "org_distinct": len(data["org"]),
        "tag_distinct": len(data["topic_tags"]),
        "tag_sum": sum(x["count"] for x in data["topic_tags"]),
        "date_range": data["date_range"],
    }
