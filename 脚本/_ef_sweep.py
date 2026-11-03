"""量 HNSW 的 `ef` 要开到多大才饱和 —— 一次跑完，模型只加载一遍。

    python 脚本/_ef_sweep.py

**为什么要专门量**：`ef` 不传时用服务端默认，评分会掉（[契约/MODULE.md](../契约/MODULE.md) §8.1）。
但「开到多少才够」随**库有多大**变 —— 语料一扩就得重测，不能把上一次的数当结论抄。
`_eval_retrieval.py` 用的是固定 `ef=256`，那个值的依据就是这份扫描。

**和 `_eval_retrieval.py` 的关系**：判据、筛选口径、`QUERY_PREFIX` 全部沿用它的，
连题目都是 `import` 它来读的 —— 不另抄一份，免得两边口径漂移后量出来的数不可比。
它每次跑都要重载 bge-m3（约半分钟），扫六七个 `ef` 就是六七遍；这里加载一次循环完。

**别拿它写文件**：结果只打印，给人看。要留档的是 `_eval_retrieval.py` 的
`结果_top{k}.json`（那份带 `ef` 字段，是正式证据）。
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "脚本"))

from _eval_retrieval import (  # noqa: E402  判据口径只此一份
    COLLECTION, DEFAULT_FILTER, EMBED_MODEL, QUERY_PREFIX, load_questions,
)

# 0 = 不传 ef，走服务端默认。两边都要量：不传是**最容易犯的错**，
# 传了才对是「知道要传但不知道传多少」。缺了 0 这一档，就看不出这个坑多深。
EF_LIST = [0, 8, 16, 32, 64, 128, 256, 512]


def main() -> None:
    from sentence_transformers import SentenceTransformer
    from pymilvus import MilvusClient

    qs = load_questions()
    scored = [q for q in qs if q["type"] != "abstain"]
    client = MilvusClient(uri="http://localhost:19530")
    if not client.has_collection(COLLECTION):
        sys.exit(f"Milvus 里没有集合 {COLLECTION}，先跑 脚本/ingest.py")
    n_rows = client.query(COLLECTION, filter="clause_id != ''",
                          output_fields=["count(*)"])[0]["count(*)"]

    print(f"库 {n_rows} 条　题目 {len(scored)} 条计入命中率　k=5")
    print(f"加载 {EMBED_MODEL} …")
    model = SentenceTransformer(EMBED_MODEL, device="cpu")

    # 向量只算一次：下面每一档用的是**同一批查询向量**，
    # 唯一变量是 ef。否则分差里混进了编码的随机性，说不清是谁造成的。
    vecs = [model.encode(QUERY_PREFIX + q["question"], normalize_embeddings=True).tolist()
            for q in scored]

    print(f"\n{'ef':>6}{'top-5':>12}{'top-1':>10}{'MRR':>9}")
    for ef in EF_LIST:
        sp: dict = {"metric_type": "COSINE"}
        if ef:
            sp["params"] = {"ef": ef}
        hit = top1 = 0
        mrr = 0.0
        for q, vec in zip(scored, vecs):
            flt = DEFAULT_FILTER if not q["filter"] else f"({DEFAULT_FILTER}) and ({q['filter']})"
            res = client.search(COLLECTION, data=[vec], limit=5, filter=flt,
                                output_fields=["clause_id"], search_params=sp)
            got = [h["entity"]["clause_id"] for h in (res[0] if res else [])]
            rank = next((i + 1 for i, c in enumerate(got) if c in q["expect"]), None)
            if rank:
                hit += 1
                mrr += 1 / rank
                if rank == 1:
                    top1 += 1
        n = len(scored)
        tag = "  ← 不传 ef（服务端默认）" if ef == 0 else ""
        print(f"{ef if ef else '—':>6}{hit:>7}/{n:<4}{top1:>7}/{n:<3}{mrr / n:>9.3f}{tag}")

    print("\n**看的是「哪一档开始不再变」**，不是「哪一档最高」。"
          "\n饱和点之后再加 ef 只是白花检索时间，前者才是该写进契约的值。")


if __name__ == "__main__":
    main()