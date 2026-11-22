"""生成前端 mock 数据 `frontend/src/api/mock/fixtures.json`。

数据来源**只允许这两处**（契约/MODULE.md §6「不要自造」）：
  产出/chunks/{doc_id}.jsonl    → clause_id / clause_no / chapter / text / clause_index
  产出/语料元数据.xlsx           → doc_type / domain / org / doc_number / 三个日期 / status / source_url / fetched_at

chunk 里**没有**元数据列（实测只有 9 个键），所以必须按 doc_id 合并 xlsx，缺一不可。

用法：
    python 脚本/_export_frontend_fixtures.py            # 写 fixtures.json
    python 脚本/_export_frontend_fixtures.py --dry-run  # 只打印统计

不写随机数、不写"大概长这样"的占位值：每条都能回到上面两个文件里的某一行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = os.path.join(ROOT, "产出", "chunks")
META = os.path.join(ROOT, "产出", "语料元数据.xlsx")
OUT = os.path.join(ROOT, "frontend", "src", "api", "mock", "fixtures.json")

# ---- 1. 元数据 ----
wb = openpyxl.load_workbook(META, data_only=True)
ws = wb.active
_rows = list(ws.iter_rows(values_only=True))
HEAD = list(_rows[0])
META_BY_DOC: dict[str, dict] = {}
for r in _rows[1:]:
    d = dict(zip(HEAD, r))
    if d.get("doc_id"):
        META_BY_DOC[str(d["doc_id"])] = d

# ---- 2. chunks ----
BY_DOC: dict[str, list[dict]] = {}
for fn in sorted(os.listdir(CHUNKS)):
    if not fn.endswith(".jsonl"):
        continue
    with open(os.path.join(CHUNKS, fn), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                BY_DOC.setdefault(row["doc_id"], []).append(row)


def brief(row: dict) -> dict:
    """chunk + xlsx → ClauseBrief（字段名与 契约/API.md §1.1 逐字一致）。"""
    m = META_BY_DOC.get(row["doc_id"], {})
    text = row.get("text", "")
    snippet = text[:200] + "..." if len(text) > 200 else text
    return {
        "clause_id": row["clause_id"],
        "doc_id": row["doc_id"],
        "doc_title": row.get("doc_title") or m.get("doc_title") or "",
        "clause_no": row.get("clause_no", ""),
        "clause_index": int(row.get("clause_index") or 0),
        "chapter": row.get("chapter"),
        "snippet": snippet,
        "score": 0.0,
        "status": m.get("status") or "有效",
        "effective_date": m.get("effective_date"),
        "source_url": m.get("source_url"),
    }


def detail(row: dict) -> dict:
    """ClauseDetail：17 个字段，与 契约/API.md §3 一致。"""
    m = META_BY_DOC[row["doc_id"]]
    return {
        "clause_id": row["clause_id"],
        "doc_id": row["doc_id"],
        "doc_title": m.get("doc_title") or row.get("doc_title") or "",
        "clause_no": row.get("clause_no", ""),
        "clause_index": int(row.get("clause_index") or 0),
        "chapter": row.get("chapter"),
        "text": row.get("text", ""),
        "doc_type": m.get("doc_type") or "",
        "domain": m.get("domain") or "",
        "org": m.get("org") or "",
        "doc_number": m.get("doc_number"),
        "publish_date": m.get("publish_date"),
        "effective_date": m.get("effective_date"),
        "expire_date": m.get("expire_date"),
        "status": m.get("status") or "有效",
        "source_url": m.get("source_url"),
        "fetched_at": m.get("fetched_at") or 0,
    }


def find_chunk(clause_id: str) -> dict:
    doc_id = clause_id.rsplit("-", 1)[0] if clause_id.count("-") == 1 else "-".join(clause_id.split("-")[:2])
    for row in BY_DOC.get(doc_id, []):
        if row["clause_id"] == clause_id:
            return row
    raise SystemExit(f"fixtures: 库里找不到 {clause_id}，先跑 pipeline.py --all")


def first_chunk_of_domain(domain: str) -> dict:
    """该 domain 下第一条带 chapter 的 chunk（优先章节完整，卡片好看）。"""
    for doc_id, rows in BY_DOC.items():
        m = META_BY_DOC.get(doc_id, {})
        if m.get("domain") != domain:
            continue
        for r in rows:
            if r.get("chapter") and len(r.get("text", "")) > 60:
                return r
    raise SystemExit(f"fixtures: domain={domain} 没有可用 chunk")


def _find_split_siblings() -> tuple[dict, dict] | None:
    """找一对真实的超长条二次切片（`...-1` / `...-2`），给「同条」关系用。

    v1 的 mock 把基础条自己当「同条」返回，点进去还是同一条 —— 看起来是数据错。
    """
    for rows in BY_DOC.values():
        ids = {r["clause_id"] for r in rows}
        for r in rows:
            cid = r["clause_id"]
            if cid.endswith("-1"):
                mate = cid[:-2] + "-2"
                if mate in ids:
                    for r2 in rows:
                        if r2["clause_id"] == mate:
                            return r, r2
    return None


def main() -> int:
    dry = "--dry-run" in sys.argv

    # ---- 详情：2 条人工点名的真值 + 每个 domain 各 1 条 ----
    detail_ids = ["NFRA-016-A26", "NPC-006-A39"]
    details = [detail(find_chunk(cid)) for cid in detail_ids]
    for dom in ("银行", "证券", "保险", "外汇", "综合"):
        row = first_chunk_of_domain(dom)
        if row["clause_id"] not in detail_ids:
            details.append(detail(row))
            detail_ids.append(row["clause_id"])

    # ---- 检索结果 40 条：跨 domain 取样 + 覆盖 null 场景 ----
    # 取样必须**跨文档**：只从同一个 doc 取，前端按 domain 筛选时会瞬间 0 条，
    # 看起来像"筛选坏了"，其实是 mock 数据没覆盖。
    null_url_docs = {d for d, m in META_BY_DOC.items() if not m.get("source_url")}
    all_rows = [r for rows in BY_DOC.values() for r in rows]
    search_rows: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> None:
        if row["clause_id"] not in seen and len(search_rows) < 40:
            search_rows.append(row)
            seen.add(row["clause_id"])

    # 1) 7 条详情一定在里面（前端从检索结果点进详情时最常用）
    for cid in detail_ids:
        add(find_chunk(cid))
    # 2) **每个 domain 取 4 条**：不这样取样，前端按 domain 一筛就 0 条，
    #    看起来像"筛选坏了"，其实是 mock 数据没覆盖。
    docs_by_domain: dict[str, list[str]] = {}
    for doc_id, m in META_BY_DOC.items():
        docs_by_domain.setdefault(str(m.get("domain") or ""), []).append(doc_id)
    for dom, docs in sorted(docs_by_domain.items()):
        # 有 source_url 的优先，否则 40 条里会有大半显示"来源页未收录"（真实覆盖率是 88.7%）
        docs = sorted(docs, key=lambda d: (not META_BY_DOC[d].get("source_url"), d))
        picked = 0
        for doc_id in docs:
            for row in BY_DOC.get(doc_id, []):
                if len(row.get("text", "")) >= 60:
                    add(row)
                    picked += 1
                    break
            if picked >= 4:
                break
    # 3) source_url=null 至少 3 条（前端要能渲染"来源页未收录"）
    for r in all_rows:
        if r["doc_id"] in null_url_docs:
            add(r)
        if sum(1 for x in search_rows if x["doc_id"] in null_url_docs) >= 3:
            break
    # 4) chapter=null 至少 2 条（前端要能处理空章节）
    for r in all_rows:
        if not r.get("chapter"):
            add(r)
        if sum(1 for x in search_rows if not x.get("chapter")) >= 2:
            break
    # 5) 还不够就补齐；**先补有 source_url 的**，否则 40 条里会有近一半显示"来源页未收录"
    for row in all_rows:
        if len(search_rows) >= 39:
            break
        if META_BY_DOC[row["doc_id"]].get("source_url"):
            add(row)
    for row in all_rows:
        if len(search_rows) >= 39:
            break
        add(row)

    # 故意留一条 XSS 测试数据：只进 mock，不进库
    xss = json.loads(json.dumps(search_rows[0]))
    xss["clause_id"] = "MOCK-XSS-1"
    xss["doc_id"] = "MOCK-XSS"
    xss["doc_title"] = "XSS 测试件"
    xss["clause_no"] = "第一条"
    xss["text"] = '测试高亮：<script>alert("xss")</script> 资本充足率不得低于百分之八。'
    search_rows.append(xss)

    search_items = []
    for i, r in enumerate(search_rows[:40]):
        b = brief(r)
        b["score"] = round(0.95 - i * 0.012, 4)
        b["highlight"] = _highlight(b["snippet"], "资本充足率")
        search_items.append({k: b[k] for k in (
            # doc_id 只给 mock 用（SearchItem 契约本身没有这个字段）；
            # mock 的 search 要靠它按 domain/status/org 过滤
            "clause_id", "doc_id", "doc_title", "clause_no", "snippet", "highlight",
            "status", "source_url", "score")})
    search_items[-1]["highlight"] = '测试高亮：<em>资本充足率</em> 不得低于百分之八。'

    # ---- 关联 6 条：四种关系都出现，且「同条」用真实的二次切片（不是自引用）----
    related_rows: list[tuple[str, dict]] = []

    # 「同条」= 同一基础锚点二次切分出的另一片（clause_id 形如 XXX-A53-1 / XXX-A53-2）
    sibling = _find_split_siblings()
    if sibling:
        related_rows.append(("同条", sibling[1]))
    # 「同章」= 本文同章的相邻条
    for cid in ("NFRA-016-A25", "NFRA-016-A27", "NFRA-016-A24"):
        try:
            related_rows.append(("同章", find_chunk(cid)))
        except SystemExit:
            continue
    # 「引用」与「相似」：mock 里只要求形状正确；真实关系由 P1 的 get_related 产出
    for rel, candidates in (("引用", ("NPC-006-A39",)), ("相似", ("PBC-001-A53", "PBC-001-A1", "NPC-006-A39"))):
        for cid in candidates:
            try:
                related_rows.append((rel, find_chunk(cid)))
                break
            except SystemExit:
                continue

    related = []
    for rel, row in related_rows[:6]:
        b = brief(row)
        b["relation_type"] = rel
        b["score"] = round(0.9 - len(related) * 0.05, 4)
        related.append(b)

    # ---- 历史 12 条 / 收藏 4 条 ----
    questions = [
        "商业银行核心一级资本充足率不得低于多少",
        "商业银行贷款应当遵守哪些资产负债比例管理规定",
        "资本充足率、一级资本充足率、核心一级资本充足率各是多少",
        "流动性资产余额与流动性负债余额的比例要求是多少",
        "对同一借款人的贷款余额与商业银行资本余额的比例上限",
        "什么是资本监管指标",
        "商业银行的资本分为哪几类",
        "风险加权资产怎么计量",
        "保险资金可以投资哪些领域",
        "证券公司净资本要求是什么",
        "跨境资金流动的管理规定",
        "金融数据安全分级怎么划分",
    ]
    history = []
    for i, q in enumerate(questions):
        d = details[i % len(details)]
        history.append({
            "qa_id": 10241 + i,
            "question": q,
            "answer": f"依据《{d['doc_title']}》{d['clause_no']}：{d['text'][:160]}",
            "score": round(0.91 - i * 0.02, 3),
            "created_at": 20261109153012 - i * 10000,
            "sources": [{"clause_id": d["clause_id"], "clause_no": d["clause_no"],
                         "doc_title": d["doc_title"]}],
        })

    favorites = []
    for i, d in enumerate(details[:4]):
        favorites.append({
            "favorite_id": 88 + i,
            "clause_id": d["clause_id"],
            "doc_title": d["doc_title"],
            "clause_no": d["clause_no"],
            # 与 /history 同口径：14 位 int，不是字符串、不是毫秒
            "created_at": 20261101102000 - i * 10000,
        })

    # ---- meta/filters：文档级口径，与 API.md §5 示例逐项一致 ----
    def axis(key: str) -> list[dict]:
        cnt: dict[str, int] = {}
        for m in META_BY_DOC.values():
            raw = str(m.get(key) or "")
            for v in (raw.replace("，", "、").split("、") if key == "org" else [raw]):
                if v.strip():
                    cnt[v.strip()] = cnt.get(v.strip(), 0) + 1
        # 与 backend/corpus_meta.doc_level_counts 同序：先按计数降序，再按取值升序（否则 56/56 并列时
        # mock 与真实接口的返回顺序会不一致，前端做字典缓存时容易误判"变了"）
        return [{"value": k, "label": k, "count": v}
                for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]

    tag_cnt: dict[str, int] = {}
    for m in META_BY_DOC.values():
        for t in str(m.get("topic_tags") or "").replace("，", ",").split(","):
            if t.strip():
                tag_cnt[t.strip()] = tag_cnt.get(t.strip(), 0) + 1
    eff = [m["effective_date"] for m in META_BY_DOC.values() if isinstance(m.get("effective_date"), int)]

    filters = {
        "domain": axis("domain"),
        "status": axis("status"),
        "org": axis("org"),
        "doc_type": axis("doc_type"),
        "topic_tags": [{"value": k, "label": k, "count": v}
                       for k, v in sorted(tag_cnt.items(), key=lambda x: (-x[1], x[0]))],
        "date_range": {"min": min(eff), "max": max(eff)},
    }

    payload = {
        "_meta": {
            "generated_at": "2026-11-09",
            "sources": ["产出/chunks/*.jsonl", "产出/语料元数据.xlsx"],
            "chunk_total": sum(len(v) for v in BY_DOC.values()),
            "doc_total": len(META_BY_DOC),
            "note": "MOCK-XSS-1 是刻意造的 XSS 测试数据，只进 mock，不入库",
        },
        # mock 的 search 要按 domain/status/org 过滤，SearchItem 本身不带这三个字段，
        # 所以单独给一份 doc 级映射（159 条，约 15KB）
        "doc_meta": {
            doc_id: {
                "doc_title": m.get("doc_title"),
                "doc_type": m.get("doc_type"),
                "domain": m.get("domain"),
                "org": m.get("org"),
                "status": m.get("status"),
                "publish_date": m.get("publish_date"),
                "effective_date": m.get("effective_date"),
                "source_url": m.get("source_url"),
            }
            for doc_id, m in META_BY_DOC.items()
        },
        "filters": filters,
        "details": details,
        "search_items": search_items,
        "related": related,
        "history": history,
        "favorites": favorites,
        "health": {"service": "ok", "mysql": {"status": "mock", "latency_ms": 1},
                   "milvus": {"status": "mock", "latency_ms": 1,
                              "collection": "finance_clause",
                              "row_count": sum(len(v) for v in BY_DOC.values())},
                   "version": "0.1.0", "mode": "mock"},
    }

    print(f"chunks={payload['_meta']['chunk_total']} docs={payload['_meta']['doc_total']}")
    print(f"details={len(details)} search={len(search_items)} related={len(related)} "
          f"history={len(history)} favorites={len(favorites)}")
    print("filters:", {k: (len(v) if isinstance(v, list) else v) for k, v in filters.items()})
    if dry:
        print("[dry-run] 未写文件")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"已写 {os.path.relpath(OUT, ROOT)}  ({os.path.getsize(OUT) / 1024:.1f} KB)")
    return 0


def _highlight(text: str, kw: str) -> str:
    """与后端 retriever._make_highlight 同口径：先转义再包 <em>。"""
    import html
    esc, esk = html.escape(text), html.escape(kw)
    return esc.replace(esk, f"<em>{esk}</em>")


if __name__ == "__main__":
    raise SystemExit(main())
