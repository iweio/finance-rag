"""第 7 步：向量化。`产出/chunks/*.jsonl` → `产出/embeddings/`。

输入 7949 个 chunk，对每个 chunk 的 **`embed_text`**（不是 `text`）编码成 1024 维向量。

**三条不可违反（[契约/MODULE.md](../契约/MODULE.md) §8）：**

1. **passage 侧不加 `QUERY_PREFIX`。** 前缀只加在 query 侧。本文件从头到尾不引用它 ——
   加错任何一侧都会掉点，而且不报错，只表现为「怎么召回的都不对」。
2. **`normalize_embeddings=True`**，与 Milvus 的 `metric_type="COSINE"` 配对
   （[契约/DB.md](../契约/DB.md) §3.3）。
3. **编码的是 `embed_text`（带上下文头），不是 `text`。** 头是「《制度名》章节 条款号」，
   让「第五十三条」这种孤立条文号在向量里带上出处。`text` 原样入库供前端展示，
   两个字段分开存的原因见 [契约/DB.md](../契约/DB.md) §3.2。

用法：

    python 脚本/embed.py               # 全量（已有分片自动跳过，可中断续跑）
    python 脚本/embed.py --limit 64    # 只编 64 条，验通路
    python 脚本/embed.py --redo        # 忽略已有分片，全部重编

**改了模型或维度必须 `--redo`。** 半新半旧的向量拼在一起，检索质量会静默劣化。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "产出", "embeddings")
PARTS = os.path.join(OUT, "parts")

SHARD = 1000  # 每个分片多少条，便于中断续跑


def load_chunks() -> list[dict]:
    """按文件名排序读全部 chunk。顺序即 Milvus 的插入顺序，必须稳定。"""
    rows: list[dict] = []
    for fp in sorted(glob.glob(os.path.join(ROOT, "产出", "chunks", "*.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def build_encoder(model_name: str):
    """加载 sentence-transformers 模型。缺依赖时给出可照做的提示，不抛裸 ImportError。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        sys.exit(
            "缺 sentence-transformers。装：\n"
            "  pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
            "  pip install sentence-transformers\n"
            "bge-m3 首次运行会从 HuggingFace 下载约 2.3GB；国内建议先设\n"
            "  set HF_ENDPOINT=https://hf-mirror.com")
    print(f"加载 {model_name} …（首次会下载约 2.3GB）")
    return SentenceTransformer(model_name, device="cpu")


def main() -> None:
    # stdout 重定向到文件时默认是**块缓冲**，进度要攒满 8KB 才落盘 ——
    # 全量跑 30 分钟，日志里却一条进度都看不到（实测踩到）。改成行缓冲。
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只编前 N 条，验通路用")
    ap.add_argument("--redo", action="store_true", help="忽略已有分片，全部重编")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--model", default=os.environ.get("EMBED_MODEL", "BAAI/bge-m3"))
    args = ap.parse_args()

    dim_expect = int(os.environ.get("EMBED_DIM", "1024"))

    rows = load_chunks()
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        sys.exit("产出/chunks 为空，先跑 python 脚本/pipeline.py --all")
    print(f"{len(rows)} 条待编码，模型 {args.model}")

    # 空 embed_text 会让向量退化成噪声，且不报错 —— 先拦住
    blank = [r["clause_id"] for r in rows if not (r.get("embed_text") or "").strip()]
    if blank:
        sys.exit(f"有 {len(blank)} 条 embed_text 为空，前几个：{blank[:5]}")

    os.makedirs(PARTS, exist_ok=True)
    model = build_encoder(args.model)

    n_shard = (len(rows) + SHARD - 1) // SHARD
    done = 0
    t0 = time.time()
    for s in range(n_shard):
        part = rows[s * SHARD : (s + 1) * SHARD]
        npy = os.path.join(PARTS, f"part-{s:03d}.npy")
        jl = os.path.join(PARTS, f"part-{s:03d}.jsonl")

        if not args.redo and os.path.exists(npy) and os.path.exists(jl):
            n = sum(1 for _ in open(jl, encoding="utf-8"))
            if n == len(part):
                print(f"  分片 {s:03d} 已有（{n} 条），跳过")
                done += n
                continue

        vecs = model.encode(
            [r["embed_text"] for r in part],   # ← embed_text，且不加任何前缀
            batch_size=args.batch,
            normalize_embeddings=True,         # ← 与 COSINE 配对
            show_progress_bar=False,
        ).astype(np.float32)

        if vecs.shape[1] != dim_expect:
            sys.exit(f"维度不符：模型出 {vecs.shape[1]}，EMBED_DIM={dim_expect}。"
                     f"换模型必须同时改 .env 并 --redo")

        np.save(npy, vecs)
        with open(jl, "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps({"clause_id": r["clause_id"]}, ensure_ascii=False) + "\n")
        done += len(part)
        el = time.time() - t0
        rate = done / el if el else 0
        print(f"  分片 {s:03d}  {len(part)} 条  累计 {done}/{len(rows)}  "
              f"{rate:.1f} 条/秒  预计剩余 {(len(rows)-done)/rate/60:.1f} 分钟"
              if rate else f"  分片 {s:03d}  {len(part)} 条")

    # ---- 收尾校验：三个数必须自洽，任一对不上就中止 ----
    vecs = []
    ids: list[str] = []
    for s in range(n_shard):
        vecs.append(np.load(os.path.join(PARTS, f"part-{s:03d}.npy")))
        with open(os.path.join(PARTS, f"part-{s:03d}.jsonl"), encoding="utf-8") as f:
            ids += [json.loads(x)["clause_id"] for x in f if x.strip()]
    V = np.vstack(vecs)
    norms = np.linalg.norm(V, axis=1)

    print(f"\n向量 {V.shape}  期望 ({len(rows)}, {dim_expect})")
    problems = []
    if V.shape != (len(rows), dim_expect):
        problems.append(f"形状 {V.shape} != ({len(rows)}, {dim_expect})")
    if ids != [r["clause_id"] for r in rows]:
        problems.append("clause_id 顺序与 chunk 不一致（插入会错位）")
    if len(set(ids)) != len(ids):
        problems.append("clause_id 有重复")
    if not np.allclose(norms, 1.0, atol=1e-3):
        problems.append(f"范数未归一：min {norms.min():.4f} max {norms.max():.4f}")
    if problems:
        sys.exit("### 校验未过，产物不可用：\n" + "\n".join("   - " + p for p in problems))

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "dim": dim_expect, "normalize": True,
                   "query_prefix_used": False, "shard": SHARD, "shards": n_shard,
                   "total": len(rows)}, f, ensure_ascii=False, indent=2)

    print(f"\n### 校验全过：{len(rows)} 条 × {dim_expect} 维，范数 "
          f"[{norms.min():.4f}, {norms.max():.4f}]，clause_id 顺序一致")
    print(f"产物：{PARTS}\n      {os.path.join(OUT, 'manifest.json')}")
    print(f"耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()