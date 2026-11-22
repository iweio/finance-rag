"""读 `产出/语料元数据.xlsx` 的纯数据层（不依赖 FastAPI / SQLAlchemy）。

拆出来的原因：`backend/meta_dict.py` 要把它包成 HTTP 错误码，`脚本/seed_mysql.py` 要拿它灌
`knowledge_point`，而后者在**没有装 fastapi 的机器**上也应该能 `--dry-run` 报数。
两边共用这一份，避免"合并逻辑写两遍、两处不一样"。

只依赖 openpyxl（requirements 里本来就有）。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "产出")))
XLSX = OUTPUT_DIR / "语料元数据.xlsx"

_LOCK = threading.Lock()
_CACHE: dict = {"mtime": None, "by_doc": None}


class CorpusMetaError(RuntimeError):
    """语料元数据表缺失/不可读。上层负责转成自己的错误类型。"""


def split_multi(value: object, seps: str = "、,，") -> list[str]:
    raw = str(value or "")
    for s in seps:
        raw = raw.replace(s, "\x00")
    return [x.strip() for x in raw.split("\x00") if x.strip()]


def current_mtime() -> float:
    """语料表的 mtime，供上层缓存失效判断（表换了/补了新件要重算字典）。"""
    try:
        return XLSX.stat().st_mtime
    except FileNotFoundError as exc:
        raise CorpusMetaError(f"语料元数据表不存在：{XLSX}") from exc


def load_meta_by_doc() -> dict[str, dict]:
    """{doc_id: {列名: 值}}。按 mtime 缓存，语料更新后自动失效。"""
    try:
        mtime = XLSX.stat().st_mtime
    except FileNotFoundError as exc:
        raise CorpusMetaError(f"语料元数据表不存在：{XLSX}") from exc

    with _LOCK:
        if _CACHE["mtime"] == mtime and _CACHE["by_doc"] is not None:
            return _CACHE["by_doc"]

        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise CorpusMetaError("缺 openpyxl，无法读语料元数据表") from exc

        try:
            wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
            ws = wb.active
            rows = ws.iter_rows(values_only=True)
            head = list(next(rows))
            by_doc: dict[str, dict] = {}
            for row in rows:
                item = dict(zip(head, row))
                doc_id = item.get("doc_id")
                if doc_id:
                    by_doc[str(doc_id)] = item
            wb.close()
        except Exception as exc:  # noqa: BLE001 - 文件损坏/格式变了都要有可读的错
            raise CorpusMetaError(f"读语料元数据表失败：{exc}") from exc

        _CACHE.update(mtime=mtime, by_doc=by_doc)
        logger.info("语料元数据已加载：%d 份", len(by_doc))
        return by_doc


def doc_level_counts(by_doc: dict[str, dict], key: str, multi: bool) -> list[dict]:
    """文档级计数（合计 = 文档份数）。`multi=True` 时按顿号/逗号拆开分别计数。"""
    counter: dict[str, int] = {}
    for meta in by_doc.values():
        values = split_multi(meta.get(key)) if multi else [str(meta.get(key) or "").strip()]
        for value in values:
            if value:
                counter[value] = counter.get(value, 0) + 1
    return [
        {"value": v, "label": v, "count": c}
        for v, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
