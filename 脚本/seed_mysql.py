"""把 `产出/chunks/*.jsonl` + `产出/语料元数据.xlsx` 灌进 MySQL 的 `knowledge_point`（B21）。

为什么需要它：`契约/DB.md §2.2` 定义了这张表（「与 Milvus 的 clause 一一对应」），但仓库里
**从来没有任何写入方**，所以它是一张空表；而 `/api/v1/meta/filters` 曾经还要查它。现在
`/meta/filters` 已改读 xlsx（B2），这张表只用于「交付物 #3 的 DDL 有数据」这一条验收证据。

数据只能从两处来，缺一不可：
    chunks    → clause_id / doc_id / clause_no / clause_index / chapter / chunk_type / text
    xlsx      → doc_title / doc_type / domain / org / doc_number / publish_date /
                effective_date / expire_date / status / source_url / fetched_at

用法：
    python 脚本/seed_mysql.py --dry-run      # 只报数、不连库（没装 SQLAlchemy 也能跑）
    python 脚本/seed_mysql.py                # 幂等 upsert（可反复跑）
    python 脚本/seed_mysql.py --batch 1000

幂等：按 `clause_id` 主键 `ON DUPLICATE KEY UPDATE` / SQLite `ON CONFLICT DO UPDATE`，
**不会**因为重跑产生第二行，也不会删掉别的行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.corpus_meta import load_meta_by_doc  # noqa: E402  - 与 /meta/filters 共用同一份读表逻辑

CHUNKS = os.path.join(ROOT, "产出", "chunks")

# 与 契约/DB.md §2.2 的列类型对齐；超长要当场报，不能靠 MySQL 截断/报 1406
LIMITS = {
    "clause_id": 64, "doc_id": 32, "doc_title": 256, "clause_no": 64, "chapter": 256,
    "chunk_type": 16, "doc_type": 32, "domain": 16, "org": 128, "doc_number": 128,
    "status": 16, "source_url": 512,
}
COLUMNS = [
    "clause_id", "doc_id", "doc_title", "clause_no", "clause_index", "chapter", "chunk_type",
    "text", "doc_type", "domain", "org", "doc_number", "publish_date", "effective_date",
    "expire_date", "status", "source_url", "fetched_at",
]


def build_rows() -> list[dict]:
    if not os.path.isdir(CHUNKS):
        raise SystemExit(f"chunks 目录不存在：{CHUNKS}，先跑 python 脚本/pipeline.py --all")
    meta = load_meta_by_doc()
    rows: list[dict] = []
    missing = 0
    for fn in sorted(os.listdir(CHUNKS)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(CHUNKS, fn), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                c = json.loads(line)
                m = meta.get(c["doc_id"])
                if m is None:
                    missing += 1
                    continue
                rows.append({
                    "clause_id": c["clause_id"],
                    "doc_id": c["doc_id"],
                    "doc_title": m.get("doc_title") or c.get("doc_title") or "",
                    "clause_no": c.get("clause_no", ""),
                    "clause_index": int(c.get("clause_index") or 0),
                    "chapter": c.get("chapter"),
                    "chunk_type": c.get("chunk_type") or "clause",
                    "text": c.get("text", ""),
                    "doc_type": m.get("doc_type") or "",
                    "domain": m.get("domain") or "",
                    "org": m.get("org") or "",
                    "doc_number": m.get("doc_number"),
                    "publish_date": m.get("publish_date"),
                    "effective_date": m.get("effective_date"),
                    "expire_date": m.get("expire_date"),
                    "status": m.get("status") or "",
                    "source_url": m.get("source_url"),
                    "fetched_at": m.get("fetched_at") or 0,
                })
    if missing:
        print(f"[WARN] {missing} 条 chunk 在 xlsx 里找不到对应 doc_id，已跳过（先检查语料表）")
    return rows


def check_limits(rows: list[dict]) -> list[str]:
    bad = []
    for key, limit in LIMITS.items():
        worst = max((len(str(r.get(key) or "")) for r in rows), default=0)
        if worst > limit:
            bad.append(f"{key}: {worst} > {limit}")
    return bad


def upsert(rows: list[dict], batch: int) -> int:
    from sqlalchemy import func
    from sqlalchemy.orm import Session

    from backend.database import SessionLocal, engine, init_db
    from backend.models import KnowledgePoint

    init_db()
    dialect = engine.dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        raise SystemExit(f"不支持的数据库方言：{dialect}（只测过 MySQL / SQLite）")

    total = 0
    with SessionLocal() as db:  # type: Session
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            stmt = dialect_insert(KnowledgePoint).values(chunk)
            if dialect == "mysql":
                stmt = stmt.on_duplicate_key_update(
                    **{c: stmt.inserted[c] for c in COLUMNS if c != "clause_id"}
                )
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["clause_id"],
                    set_={c: stmt.excluded[c] for c in COLUMNS if c != "clause_id"},
                )
            db.execute(stmt)
            db.commit()
            total += len(chunk)
            print(f"  已 upsert {total}/{len(rows)}")
        count = db.query(func.count(KnowledgePoint.clause_id)).scalar() or 0
    print(f"knowledge_point 现有 {count} 行（本次写入 {total} 行，幂等）")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="把语料灌进 MySQL knowledge_point")
    ap.add_argument("--dry-run", action="store_true", help="只报数，不连库")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    rows = build_rows()
    print(f"chunks 合并完成：{len(rows)} 行")
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["chunk_type"]] = by_type.get(r["chunk_type"], 0) + 1
    print(f"chunk_type 分布：{by_type}")

    bad = check_limits(rows)
    if bad:
        print("[FAIL] 字段超长（MySQL 会报 1406 / 截断）：" + "; ".join(bad))
        return 1
    print("长度预检通过（按字符数，与 DB.md 的 VARCHAR(n) 同口径）")

    if args.dry_run:
        print("[dry-run] 未连库")
        return 0
    upsert(rows, args.batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
