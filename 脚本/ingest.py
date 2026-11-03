"""第 8 步：入 Milvus。`产出/embeddings/` + `产出/chunks/` + 语料元数据 → collection `finance_clause`。

**字段名逐字取自 [契约/DB.md](../契约/DB.md) §3.1**，本文件不自己起名字。

插入用**行式字典**（`list[dict]`），字段名就是字典的键，对不上会直接报错。
**不能用列式**（`list[list]`）：pymilvus 对列式只比对字段**个数**、不比对字段**名**
（`orm/schema.py` 的 `_check_data_schema_cnt` 只在 DataFrame 分支才查列名），
列顺序写错会静默把整批行的字段串位，Milvus 不报错。

**服务端与客户端都必须 2.5 线**：2.4.x 的 `FieldSchema` 会把 `nullable` 静默吞掉
（`entity_helper.py` 里 `valid_data` 0 处），标了 nullable 的字段实际不可为空，
行式列式两条路都拒 `None`。见 [契约/DB.md](../契约/DB.md) §3.1.2。

**三道闸门，任一不过就中止：**

1. **字节预检**：每个 VARCHAR 按 UTF-8 字节比对 DB.md 的 `max_length`。`org` 曾写 128
   而实测 174 字节，就是这道闸门要拦的（§3.1.1）。
2. **对齐预检**：向量条数、clause_id 顺序、chunk 条数三者一致，错一个就整批串行。
3. **落库复核**：插入后查 `count(*)`，必须 **8156**（[契约/DB.md](../契约/DB.md) §5）。
   语料一变这个数就要跟着改（脚本 `_counts.py` 报的数），别当成恒定值。

用法：

    python 脚本/ingest.py --recreate     # 删表重建（首次或重灌）
    python 脚本/ingest.py                # 灌进已有表

连不上时先把 Milvus 起来：`docker compose up -d milvus etcd minio`
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "脚本"))
from pipeline import load_meta  # 元数据表的唯一读法，别再写第二份

EXPECT_ROWS = 8156  # 入库行数（锚点 8036 + 超长条二次切 120）


# ---------- schema：与 DB.md §3.1 逐字对应 ----------

def build_schema(dim: int):
    from pymilvus import CollectionSchema, DataType, FieldSchema

    V, I, B, F = DataType.VARCHAR, DataType.INT64, DataType.BOOL, DataType.FLOAT_VECTOR
    fields = [
        FieldSchema("clause_id",      V, max_length=64,  is_primary=True),
        FieldSchema("embedding",      F, dim=dim),
        # ---- 文本：两个字段必须分开存（§3.2）----
        FieldSchema("text",           V, max_length=8192),
        FieldSchema("embed_text",     V, max_length=8192),
        # ---- 标量：过滤与溯源 ----
        FieldSchema("doc_id",         V, max_length=32),
        FieldSchema("doc_title",      V, max_length=256),
        FieldSchema("doc_type",       V, max_length=32),
        FieldSchema("clause_no",      V, max_length=64),
        FieldSchema("chapter",        V, max_length=256, nullable=True),
        # org 是 256 不是 128：4 份联合发文实测 174 字节（§3.1.1）
        FieldSchema("org",            V, max_length=256),
        FieldSchema("doc_number",     V, max_length=128, nullable=True),
        FieldSchema("source_url",     V, max_length=512, nullable=True),
        FieldSchema("status",         V, max_length=16),
        FieldSchema("domain",         V, max_length=16),
        FieldSchema("topic_tags",     V, max_length=256, nullable=True),
        FieldSchema("publish_date",   I, nullable=True),
        FieldSchema("effective_date", I, nullable=True),
        FieldSchema("expire_date",    I, nullable=True),
        FieldSchema("clause_index",   I),
        FieldSchema("is_short",       B),
        FieldSchema("fetched_at",     I),
    ]
    return CollectionSchema(fields, description="金融制度条文库")


def contract_limits() -> dict[str, int]:
    """从 DB.md 里读 max_length。契约文件是唯一真源，代码不另抄一份。"""
    db = open(os.path.join(ROOT, "契约", "DB.md"), encoding="utf-8").read()
    mv = db[db.find("fields = ["):db.find("schema = CollectionSchema")]
    return {m[0]: int(m[1]) for m in re.findall(
        r'FieldSchema\("([a-z_]+)",\s+DataType\.VARCHAR,\s+max_length=(\d+)', mv)}


# ---------- 装配行 ----------

def load_vectors() -> tuple[np.ndarray, list[str]]:
    """读分片向量与对应的 clause_id 顺序。"""
    parts = sorted(glob.glob(os.path.join(ROOT, "产出", "embeddings", "parts", "part-*.npy")))
    if not parts:
        sys.exit("没有向量分片，先跑 python 脚本/embed.py")
    vecs, ids = [], []
    for p in parts:
        vecs.append(np.load(p))
        with open(p[:-4] + ".jsonl", encoding="utf-8") as f:
            ids += [json.loads(x)["clause_id"] for x in f if x.strip()]
    return np.vstack(vecs), ids


def load_chunks() -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(glob.glob(os.path.join(ROOT, "产出", "chunks", "*.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def to_int(v) -> int | None:
    """空 → None（该列 nullable）。注意不能给 0：0 会被 `effective_date <= …` 当成真日期。"""
    return None if v in (None, "") else int(v)


def build_rows(chunks: list[dict], vecs: np.ndarray,
               meta: dict[str, dict]) -> list[dict]:
    """装配插入行。返回**行式字典**，字段名即字典键 —— 理由见模块开头。"""
    rows: list[dict] = []
    for c, v in zip(chunks, vecs):
        m = meta[c["doc_id"]]
        rows.append({
            "clause_id": c["clause_id"],
            "embedding": v.tolist(),
            "text": c["text"],
            "embed_text": c["embed_text"],
            "doc_id": c["doc_id"],
            "doc_title": c["doc_title"],
            "doc_type": m["doc_type"],
            "clause_no": c["clause_no"],
            # 空 → None。这几个字段在 DB.md §3.1 里标了 nullable，Milvus 2.5 收 None
            "chapter": c["chapter"] or None,
            "org": m["org"],
            "doc_number": m["doc_number"] or None,
            "source_url": m["source_url"] or None,
            "status": m["status"],
            "domain": m["domain"],
            "topic_tags": m["topic_tags"] or None,
            "publish_date": to_int(m["publish_date"]),
            "effective_date": to_int(m["effective_date"]),
            "expire_date": to_int(m["expire_date"]),
            "clause_index": int(c["clause_index"]),
            "is_short": bool(c["is_short"]),
            "fetched_at": to_int(m["fetched_at"]),
        })
    return rows


def main() -> None:
    # 重定向到日志时 stdout 是块缓冲，进度会攒满 8KB 才落盘
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true", help="删掉已有 collection 重建")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--host", default=os.environ.get("MILVUS_HOST", "localhost"))
    ap.add_argument("--port", default=os.environ.get("MILVUS_PORT", "19530"))
    ap.add_argument("--collection", default=os.environ.get("MILVUS_COLLECTION", "finance_clause"))
    args = ap.parse_args()
    dim = int(os.environ.get("EMBED_DIM", "1024"))

    try:
        import pymilvus
        from pymilvus import Collection, connections, utility
    except ImportError:
        sys.exit("缺 pymilvus。装：pip install \"pymilvus>=2.5,<2.6\"")

    # 2.4.x 的客户端不支持 nullable，插 None 时只报 "expect string input, got NoneType"，
    # 从这句完全看不出真正的原因（DB.md §3.1.2）。所以提前拦住，给能照做的提示。
    if tuple(int(x) for x in pymilvus.__version__.split(".")[:2]) < (2, 5):
        sys.exit(f"pymilvus {pymilvus.__version__} 不支持 nullable。"
                 f"装：pip install \"pymilvus>=2.5,<2.6\"")

    chunks = load_chunks()
    vecs, vec_ids = load_vectors()
    meta = load_meta()

    # ---- 闸门 1：字节预检 ----
    lim = contract_limits()
    over = []
    for f, mx in lim.items():
        worst, where = 0, ""
        for c in chunks:
            raw = c.get(f) if f in c else meta[c["doc_id"]].get(f)
            b = len(str(raw or "").encode("utf-8"))
            if b > worst:
                worst, where = b, c["clause_id"]
        if worst > mx:
            over.append(f"{f} {worst}>{mx}（{where}）")
    if over:
        sys.exit("### 闸门 1 未过：字段超 Milvus max_length（按字节，DB.md §3.1.1）\n"
                 + "\n".join("   - " + o for o in over))
    print(f"闸门 1 过：{len(lim)} 个 VARCHAR 字段均在 DB.md 的 max_length 内（按 UTF-8 字节）")

    # ---- 闸门 2：三者对齐 ----
    probs = []
    if len(chunks) != len(vec_ids) or len(chunks) != len(vecs):
        probs.append(f"条数不齐：chunk {len(chunks)} / 向量 {len(vecs)} / 向量 id {len(vec_ids)}")
    if vec_ids != [c["clause_id"] for c in chunks]:
        probs.append("clause_id 顺序与 chunk 不一致（会整批串行）")
    if len(chunks) != EXPECT_ROWS:
        probs.append(f"条数 {len(chunks)} != 期望 {EXPECT_ROWS}（DB.md §5）")
    if probs:
        sys.exit("### 闸门 2 未过：\n" + "\n".join("   - " + p for p in probs))
    print(f"闸门 2 过：{len(chunks)} 条，向量与 chunk 的 clause_id 顺序一致")

    rows = build_rows(chunks, vecs, meta)

    # ---- 连接与建表 ----
    connections.connect(alias="default", host=args.host, port=args.port)
    print(f"已连 Milvus {args.host}:{args.port}")
    if utility.has_collection(args.collection):
        if not args.recreate:
            sys.exit(f"{args.collection} 已存在。要重灌加 --recreate")
        utility.drop_collection(args.collection)
        print(f"已删旧 {args.collection}")

    schema = build_schema(dim)
    coll = Collection(args.collection, schema=schema)
    print(f"建表 {args.collection}：{len(schema.fields)} 个字段")

    for i in range(0, len(rows), args.batch):
        coll.insert(rows[i:i + args.batch])
    coll.flush()
    print(f"已插入 {len(rows)} 条并 flush")

    coll.create_index("embedding", {
        "index_type": "HNSW", "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200},     # DB.md §3.3
    })
    for f in ("status", "domain", "effective_date"):
        # 时效性过滤（差异化要求②）的落点，缺了就退化成全表扫（DB.md §3.4）
        coll.create_index(f, {"index_type": "INVERTED"})
    coll.load()
    print("已建索引：HNSW(COSINE) + 3 个 INVERTED，并 load")

    # ---- 闸门 3：落库复核 ----
    n = coll.query(expr="clause_id != ''", output_fields=["count(*)"])[0]["count(*)"]
    print(f"\n落库 count(*) = {n}，期望 {EXPECT_ROWS}")
    if n != EXPECT_ROWS:
        sys.exit(f"### 闸门 3 未过：库里 {n} 条，期望 {EXPECT_ROWS}。"
                 f"差 {EXPECT_ROWS - n} 条说明有批次丢了或重复入库")
    print("### 三道闸门全过，知识库就位")


if __name__ == "__main__":
    main()