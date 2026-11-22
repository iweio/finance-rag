"""生成质量验收脚本：50 题金标集 + 语料外拒答题，全链路调 RagEngine.answer_stream()。

输入：产出/评估集/50题.jsonl（每行 {"qid": ..., "question": ..., "expect": [clause_id, ...]}）
输出：产出/评估集/结果_generate.json + 控制台摘要（可直接贴进 产出/抽检报告.md 的"生成质量"一节）

用法（项目根目录，需先配好 .env / 环境变量）：
    python 脚本/_eval_generate.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 项目根 = 本文件 ../../
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rag_core import RagEngine  # noqa: E402

EVAL_DIR = ROOT / "产出" / "评估集"
GOLD_FILE = EVAL_DIR / "50题.jsonl"
RESULT_FILE = EVAL_DIR / "结果_generate.json"

# 语料外问题（测拒答）：答案不应出现在知识库语料中
OUT_OF_CORPUS = [
    "核心一级资本充足率不得低于多少？",
    "我国现行刑法规定的洗钱罪最高可判处多少年有期徒刑？",
    "科创板上市公司破发率达到多少时会触发中止发行？",
    "LPR 一年期报价在 2020 年 4 月下调到了多少？",
    "巴塞尔协议 III 最终版对操作风险资本的要求公式是什么？",
]


def run_one(engine: RagEngine, question: str, session_id: str) -> dict:
    t0 = time.time()
    answer_text, sources, score = [], [], 0.0
    refused = False
    for event in engine.answer_stream(question, None, session_id):
        if event.event == "delta":
            answer_text.append(event.data.get("text", ""))
        elif event.event == "sources":
            sources = event.data.get("sources", [])
        elif event.event == "score":
            score = event.data.get("score", 0.0)
    elapsed_ms = int((time.time() - t0) * 1000)
    text = "".join(answer_text)
    # 拒答判定：命中拒答话术或 sources 为空
    if not sources or "无法给出准确回答" in text or "无法回答" in text:
        refused = True
    return {
        "answer": text,
        "sources": [s.get("clause_id") for s in sources],
        "score": score,
        "refused": refused,
        "elapsed_ms": elapsed_ms,
    }


def main() -> None:
    engine = RagEngine()
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    gold_items = []
    if GOLD_FILE.exists():
        with open(GOLD_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    gold_items.append(json.loads(line))
    else:
        print(f"[warn] 未找到金标集 {GOLD_FILE}，仅跑语料外拒答题", file=sys.stderr)

    # ---- ①②④：金标题 ----
    hit_cnt, valid_cnt, latency_sum = 0, 0, 0
    per_item = []
    for it in gold_items:
        qid, question, expect = it["qid"], it["question"], it.get("expect", [])
        r = run_one(engine, question, session_id=f"eval-{qid}")
        cited = set(r["sources"])
        hit = bool(cited & set(expect))
        valid = not r["refused"]  # 引用校验已通过 generator 防线；未被降级即视为通过
        hit_cnt += hit
        valid_cnt += valid
        latency_sum += r["elapsed_ms"]
        per_item.append({
            "qid": qid, "question": question, "expect": expect,
            "hit": hit, "passed": valid, "refused": r["refused"],
            "cited": sorted(cited), "elapsed_ms": r["elapsed_ms"],
        })
        print(f"[{qid}] hit={hit} refused={r['refused']} {r['elapsed_ms']}ms  {question[:30]}")

    n = len(gold_items)
    citation_hit_rate = hit_cnt / n if n else 0.0
    validate_pass_rate = valid_cnt / n if n else 0.0

    # ---- ③：语料外拒答题 ----
    refuse_results = []
    refuse_ok = 0
    for i, q in enumerate(OUT_OF_CORPUS):
        r = run_one(engine, q, session_id=f"eval-ooc-{i}")
        ok = r["refused"]
        refuse_ok += ok
        refuse_results.append({"question": q, "refused": r["refused"], "ok": ok,
                               "answer": r["answer"][:200], "elapsed_ms": r["elapsed_ms"]})
        print(f"[ooc-{i}] refused={r['refused']} {'PASS' if ok else 'FAIL'}  {q[:30]}")
    refuse_rate = refuse_ok / len(OUT_OF_CORPUS) if OUT_OF_CORPUS else 0.0

    avg_latency_ms = latency_sum / n if n else 0.0

    result = {
        "gold_total": n,
        "citation_hit_rate": citation_hit_rate,
        "validate_pass_rate": validate_pass_rate,
        "out_of_corpus_total": len(OUT_OF_CORPUS),
        "refuse_accuracy": refuse_rate,
        "avg_latency_ms": avg_latency_ms,
        "items": per_item,
        "out_of_corpus": refuse_results,
    }
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ---- 可直接贴进抽检报告的摘要 ----
    summary = f"""## 生成质量
- 评测集：产出/评估集/50题.jsonl，共 {n} 题，直接调用 RagEngine.answer_stream()（检索+生成全链路，不走 HTTP）
- ① 答案引用命中率（sources ∩ expect 非空）：{hit_cnt}/{n} = {citation_hit_rate:.1%}
- ② 引用校验通过率（生成后经引用防线、未降级拒答）：{valid_cnt}/{n} = {validate_pass_rate:.1%}（目标 100%）
- ③ 语料外问题拒答正确率：{refuse_ok}/{len(OUT_OF_CORPUS)} = {refuse_rate:.1%}（{len(OUT_OF_CORPUS)} 道语料外硬编码题，含"核心一级资本充足率不得低于多少"）
- ④ 平均延迟：{avg_latency_ms:.0f} ms/题
- 明细：产出/评估集/结果_generate.json
"""
    print(summary)


if __name__ == "__main__":
    main()
