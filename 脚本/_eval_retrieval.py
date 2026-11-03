"""第 9 步的验收台：跑 50 题评估集，算 top-k 命中率（要求 ≥ 80%）。

**只做向量检索这一段。** 第 9 步的完整链路是「向量 + BM25 → RRF → rerank」，
其中 BM25 / RRF / rerank 属 P1 的实现（[契约/MODULE.md](../契约/MODULE.md)），
本脚本量的是**向量这一路单独的基线**。P1 的混合检索叠上去只会更好，
但基线本身不能低于 80%，否则说明语料或切分有问题，不是融合算法能救的。

**查询侧三件不能错**（错一件就掉点，且不报错、只表现为「召回的都不对」）：

1. 加 `QUERY_PREFIX` —— 只加 query 侧，passage 侧不加（`embed.py` 里刻意不引用它）
2. `normalize_embeddings=True` —— 与 Milvus 的 `COSINE` 配对
3. 模型必须是 `BAAI/bge-m3` —— 与入库时**同一个**，换了模型等于两套空间

**题目文件**：`产出/评估集/50题.jsonl`，一行一题：

    {"qid":"Q01","type":"fact","question":"…","expect":["PBC-001-A53"],"filter":null}

`type` 取三值，**只有前两类计入命中率**：

| type | 含义 | 判定 |
|---|---|---|
| `fact` | 单条事实型 | 期望条文出现在 top-k 里就算命中 |
| `filter` | 带筛选项（`domain` / `org` / `status`） | 同上，但先过滤后检索 |
| `abstain` | 语料里没有，**正确行为是拒答** | **不计入命中率**，单列 —— 它测的是第 10 步的拒答，不是检索 |

**6 道 `filter` 题故意复用对应 `fact` 题的同一根条文**（`Q27`→`Q41` 等），
两边都留着不是冗余：`fact` 量的是「不筛能不能找到」，`filter` 量的是
**「加上筛选项之后会不会连对的那条都被筛掉」**——后者是筛选项写窄了会静默返回空的那种坏法。
另有闸门：所有 `expect` 必须真的存在于库里，写错一个字符直接退出，不当成「未命中」。

用法：

    python 脚本/_eval_retrieval.py            # 默认 top-5
    python 脚本/_eval_retrieval.py --k 10
    python 脚本/_eval_retrieval.py --show-miss 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Windows 控制台默认 GBK，打印 ✅ / ❌ 会 UnicodeEncodeError 直接崩在最后一行
# （结果算完了却写不出文件）。固定成 UTF-8，别再靠 emoji 赌控制台代码页。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS = os.path.join(ROOT, "产出", "评估集", "50题.jsonl")

EMBED_MODEL = "BAAI/bge-m3"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："  # ★ 只在 query 侧加
COLLECTION = "finance_clause"
# 与 ingest.py 的入库口径一致：有效件 + 已生效
DEFAULT_FILTER = 'status == "有效" and effective_date <= 20261027'


def load_questions() -> list[dict]:
    if not os.path.exists(QS):
        sys.exit(f"没有题目文件 {os.path.relpath(QS, ROOT)}")
    out = []
    for i, line in enumerate(open(QS, encoding="utf-8"), 1):
        if not line.strip():
            continue
        q = json.loads(line)
        for k in ("qid", "type", "question"):
            if k not in q:
                sys.exit(f"第 {i} 行缺字段 {k}")
        if q["type"] not in ("fact", "filter", "abstain"):
            sys.exit(f"第 {i} 行 type 非法：{q['type']!r}")
        q.setdefault("expect", [])
        q.setdefault("filter", None)
        q.setdefault("pair", None)  # filter 题指向它的 fact 孪生题，见文件头
        # 自洽性：abstain 不该有期望条文，其余必须有
        if q["type"] == "abstain" and q["expect"]:
            sys.exit(f"第 {i} 行标了 abstain 却给了 expect")
        if q["type"] != "abstain" and not q["expect"]:
            sys.exit(f"第 {i} 行没给 expect，那它到底期望命中什么")
        out.append(q)
    ids = [q["qid"] for q in out]
    if len(set(ids)) != len(ids):
        sys.exit("qid 有重复")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5, help="top-k，默认 5（契约要求 top5 ≥ 80%%）")
    ap.add_argument("--show-miss", type=int, default=15, help="最多打印几条未命中")
    # HNSW 是**近似**索引，且建图本身带随机性 —— 同一批向量重建一次索引，
    # 排位边界上的结果就会翻转（实测：纯重灌一次，命中率从 46/46 掉到 45/46）。
    # 所以评测必须**把 ef 固定住**，否则量出来的分不可复现、也没法跨人比较。
    ap.add_argument("--ef", type=int, default=256,
                    help="HNSW 检索 ef，默认 256（远大于 k，近似当精确用）。设 0 则不传、用服务端默认")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    from pymilvus import MilvusClient

    qs = load_questions()
    client = MilvusClient(uri="http://localhost:19530")
    if not client.has_collection(COLLECTION):
        sys.exit(f"Milvus 里没有集合 {COLLECTION}，先跑 脚本/ingest.py")

    # ★ 闸门：每个 expect 必须在库里真的存在。
    #   漏了这道，题目里写错一个字符（`A53` 敲成 `A54`）不报错，
    #   只表现为「这条没命中」—— 会被当成检索不好，去调模型，南辕北辙。
    want = sorted({e for q in qs for e in q["expect"]})
    have: set[str] = set()
    for i in range(0, len(want), 40):
        chunk = want[i : i + 40]
        expr = "clause_id in [" + ",".join(json.dumps(c) for c in chunk) + "]"
        for r in client.query(COLLECTION, filter=expr, output_fields=["clause_id"], limit=100):
            have.add(r["clause_id"])
    absent = [c for c in want if c not in have]
    if absent:
        sys.exit(f"题目里 {len(absent)} 个 expect 在库里不存在（题目写错了）：{absent}")
    print(f"expect 自检：{len(want)} 个期望条文全部存在于库中"
          f"（{len(qs)} 题共 {sum(len(q['expect']) for q in qs)} 处引用）")

    print(f"加载 {EMBED_MODEL} …")
    model = SentenceTransformer(EMBED_MODEL, device="cpu")

    scored = [q for q in qs if q["type"] != "abstain"]
    print(f"题目 {len(qs)} 条：计入命中率 {len(scored)}，拒答题 {len(qs) - len(scored)}\n")

    hits: list[dict] = []
    miss: list[tuple[dict, list[str]]] = []
    for q in scored:
        vec = model.encode(QUERY_PREFIX + q["question"], normalize_embeddings=True)
        # 题目自带的筛选是**叠加**在默认条件上的，不是替换 ——
        # 替换掉的话「有效且已生效」就没了，未生效/已废止的件会被召回，结果对不上真实链路
        flt = DEFAULT_FILTER if not q["filter"] else f"({DEFAULT_FILTER}) and ({q['filter']})"
        sp = {"metric_type": "COSINE"}
        if args.ef:
            sp["params"] = {"ef": args.ef}
        res = client.search(
            collection_name=COLLECTION,
            data=[vec.tolist()],
            limit=args.k,
            filter=flt,
            output_fields=["clause_id", "doc_title", "clause_no"],
            search_params=sp,
        )
        got = [h["entity"]["clause_id"] for h in (res[0] if res else [])]
        ok = [e for e in q["expect"] if e in got]
        rank = next((i + 1 for i, c in enumerate(got) if c in q["expect"]), None)
        hits.append({"qid": q["qid"], "type": q["type"], "ok": bool(ok),
                     "rank": rank, "expect": q["expect"], "got": got})
        if not ok:
            miss.append((q, got))

    hit = sum(1 for h in hits if h["ok"])
    rate = hit / len(hits) if hits else 0.0
    mrr = sum(1 / h["rank"] for h in hits if h["rank"]) / len(hits) if hits else 0.0
    # top-1 单独看：模型是不是「知道」但排位靠后，这决定要不要上 rerank
    top1 = sum(1 for h in hits if h["rank"] == 1)

    print(f"### top-{args.k} 命中率 {hit}/{len(hits)} = {rate:.1%}"
          f"　（契约要求 ≥ 80%）{'  [达标]' if rate >= 0.80 else '  [未达标]'}")
    print(f"    top-1 {top1}/{len(hits)} = {top1/len(hits):.1%}　MRR {mrr:.3f}")
    for t in ("fact", "filter"):
        sub = [h for h in hits if h["type"] == t]
        if sub:
            s = sum(1 for h in sub if h["ok"])
            print(f"    {t:7s} {s}/{len(sub)} = {s/len(sub):.1%}")

    if miss:
        print(f"\n### 未命中 {len(miss)} 条（列前 {min(args.show_miss, len(miss))} 条）")
        for q, got in miss[: args.show_miss]:
            print(f"  {q['qid']} 期望 {q['expect']}")
            print(f"       问：{q['question']}")
            print(f"       得：{got[:3]}")

    # 配对检查：filter 题与其 fact 孪生题**逐字同问**，只差一个筛选项。
# 两者结果不一致，说明这个筛选项真的改动了候选集（这正是要量的事）；
# filter 挂了而孪生题过了，就是筛选项把答案筛掉了 —— 这种坏法不报错、只返回空。
    by_qid = {h["qid"]: h for h in hits}
    pairs = [(q["qid"], q["pair"]) for q in qs if q.get("pair")]
    if pairs:
        print(f"\n### 筛选项配对（{len(pairs)} 对，同一问句 ± 筛选项）")
        for fq, tq in pairs:
            f, t = by_qid.get(fq), by_qid.get(tq)
            if not f or not t:
                continue
            same = f["got"] == t["got"]
            flag = ""
            if not f["ok"] and t["ok"]:
                flag = "  ★ 筛选项把答案筛掉了"
            print(f"  {fq}(筛) vs {tq}(不筛)：结果{'相同' if same else '不同'}"
                  f"　命中 {f['rank'] or '—'} / {t['rank'] or '—'}{flag}")

    ab = [q for q in qs if q["type"] == "abstain"]
    if ab:
        print(f"\n### 拒答题 {len(ab)} 条（不计入命中率，测的是第 10 步的拒答）")
        for q in ab:
            print(f"  {q['qid']} {q['question']}")

    out = os.path.join(ROOT, "产出", "评估集", f"结果_top{args.k}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"k": args.k, "ef": args.ef, "metric": "COSINE", "model": EMBED_MODEL,
                   "query_prefix": QUERY_PREFIX, "default_filter": DEFAULT_FILTER,
                   "hit_rate": rate, "top1": top1 / len(hits), "mrr": mrr,
                   "n": len(hits), "detail": hits}, f, ensure_ascii=False, indent=2)
    print(f"\n明细写入 {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()