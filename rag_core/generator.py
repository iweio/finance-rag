"""LLM 流式生成。

接收检索到的条文 + 用户问题，拼 Prompt 调 LLM，流式吐字。
SSE 事件顺序固定：delta* → sources → score → done。

引用校验防线（本次新增）：
- Prompt 强制模型用 《法规名》[编号] 第X条 格式引用，编号对应上下文 [i]。
- 生成结束后用正则提取引用，校验编号 ⊆ 本次检索返回的条文集合：
    * 越界 → 剔除越界引用句并重生成一次；
    * 仍越界 → 降级为拒答，并记日志统计幻觉次数。
无依据拒答：检索不到相关内容时输出拒答话术，sources 为空数组，score 低于阈值。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Iterator

from rag_core.models import ClauseBrief, Event

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "dashscope")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# 生成参数：temperature 调低压低幻觉；超时 / 重试可经环境变量覆盖
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))

REFUSAL_TEXT = (
    "抱歉，当前知识库中未找到与您问题直接相关的制度条文，无法给出准确回答。"
    "建议您调整问题措辞，或咨询专业法律人员。"
)
CONFIDENCE_THRESHOLD = 0.3


def _build_prompt(question: str, clauses: list[ClauseBrief]) -> str:
    if not clauses:
        return (
            "你是一个专业的金融法规知识库问答助手。请严格依据以下给定条文回答用户问题。"
            "如果给定条文中没有与问题相关的内容，请明确告知用户无法回答，不要编造信息。\n\n"
            f"用户问题：{question}\n\n"
            "给定条文：无\n\n"
            "请回答："
        )

    context_parts = []
    for i, c in enumerate(clauses, 1):
        source = f"（来源：{c.doc_title} {c.clause_no}）" if c.doc_title else ""
        context_parts.append(f"[{i}] {c.snippet} {source}")
    context = "\n".join(context_parts)

    return (
        "你是一个专业的金融法规知识库问答助手。请严格依据以下给定条文回答用户问题。\n"
        "要求：\n"
        "1. 只使用给定条文中的信息作答，不要编造或补充条文未涉及的内容。\n"
        "2. 回答中引用条文时，必须使用格式：《法规名》[编号] 第X条，其中编号就是给定条文前的方括号编号。\n"
        "3. 引用只能来自上面给定的条文，编号不得超出给定范围，不得引用上下文中不存在的条文。\n"
        "4. 如果给定条文与问题无关或信息不足，请明确说明无法回答。\n"
        "5. 语言简洁准确，使用中文。\n\n"
        f"用户问题：{question}\n\n"
        f"给定条文：\n{context}\n\n"
        "请回答："
    )


# ---- 引用提取 / 校验 ----

# 匹配 《法规名》[编号] 第X条 与 《法规名》[编号] 两种形态
_CITE_FULL_RE = re.compile(r"《(?P<title>[^》]+)》\[(?P<idx>\d+)\]\s*第(?P<art>\d+)条")
_CITE_SHORT_RE = re.compile(r"《(?P<title>[^》]+)》\[(?P<idx>\d+)\]")


def _split_sentences(text: str) -> list[str]:
    """按句号/分号切句，保留引用归属的粒度。"""
    parts = re.split(r"(?<=[。；;])", text)
    return [p for p in parts if p.strip()]


def _extract_citations(answer: str) -> list[dict]:
    """从模型答案中提取全部引用：法规名 + 条号 + 上下文编号 [i]。"""
    cites = []
    for m in _CITE_FULL_RE.finditer(answer):
        cites.append({
            "title": m.group("title"),
            "article": m.group("art"),
            "idx": int(m.group("idx")),
        })
    for m in _CITE_SHORT_RE.finditer(answer):
        cites.append({
            "title": m.group("title"),
            "article": None,
            "idx": int(m.group("idx")),
        })
    return cites


def _validate_citations(answer: str, clauses: list[ClauseBrief]) -> dict:
    """校验引用编号是否 ⊆ 本次检索返回的条文集合。

    返回 {ok, out_of_range, cleaned}：
    - ok：无越界引用；
    - out_of_range：越界引用的 idx 列表；
    - cleaned：剔除越界引用句后的答案（用于重生成提示）。
    """
    max_idx = len(clauses)
    cites = _extract_citations(answer)
    bad_idx = sorted({c["idx"] for c in cites if c["idx"] < 1 or c["idx"] > max_idx})
    if not bad_idx:
        return {"ok": True, "out_of_range": [], "cleaned": answer}

    keep = []
    for sent in _split_sentences(answer):
        sent_idx = {c["idx"] for c in _extract_citations(sent)}
        if sent_idx & set(bad_idx):
            continue  # 含越界引用的整句剔除
        keep.append(sent)
    return {"ok": False, "out_of_range": bad_idx, "cleaned": "".join(keep)}


def _call_dashscope_stream(prompt: str) -> Iterator[str]:
    try:
        import dashscope
        from dashscope import Generation
    except ImportError:
        raise RuntimeError(
            "缺 dashscope。pip install dashscope"
        )

    dashscope.api_key = LLM_API_KEY
    last_err: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            responses = Generation.call(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                result_format="message",
                stream=True,
                incremental_output=True,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                timeout=LLM_TIMEOUT,
            )
            yield from _iter_dashscope_responses(responses)
            return
        except Exception as e:
            last_err = e
            logger.warning("LLM 调用失败（第 %d 次）: %s", attempt + 1, e)
    raise last_err  # 重试耗尽，由调用方包成 RagError


def _iter_dashscope_responses(responses: Iterator) -> Iterator[str]:
    for resp in responses:
        if resp.status_code != 200:
            raise RuntimeError(f"LLM 调用失败: {resp.code} {resp.message}")
        content = resp.output.choices[0].message.content
        if content:
            yield content


def _call_openai_stream(prompt: str) -> Iterator[str]:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("缺 openai。pip install openai")

    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    client = OpenAI(api_key=LLM_API_KEY, base_url=base_url, timeout=LLM_TIMEOUT)
    last_err: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            stream = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
            return
        except Exception as e:
            last_err = e
            logger.warning("LLM 调用失败（第 %d 次）: %s", attempt + 1, e)
    raise last_err


def _call_llm(prompt: str) -> Iterator[str]:
    if LLM_PROVIDER == "dashscope":
        return _call_dashscope_stream(prompt)
    elif LLM_PROVIDER == "openai":
        return _call_openai_stream(prompt)
    else:
        raise RuntimeError(f"不支持的 LLM_PROVIDER: {LLM_PROVIDER}")


def _sources_data(clauses: list[ClauseBrief]) -> list[dict]:
    return [{
        "clause_id": c.clause_id,
        "doc_id": c.doc_id,
        "doc_title": c.doc_title,
        "clause_no": c.clause_no,
        "clause_index": c.clause_index,
        "chapter": c.chapter,
        "snippet": c.snippet,
        "score": c.score,
        "status": c.status,
        "effective_date": c.effective_date,
        "source_url": c.source_url,
    } for c in clauses]


def _refusal_events(t0: float, qa_id: int, sources: list[dict], score: float) -> Iterator[Event]:
    yield Event("delta", {"text": REFUSAL_TEXT})
    yield Event("sources", {"sources": sources})
    yield Event("score", {"score": score})
    yield Event("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})


def generate_stream(
    question: str,
    clauses: list[ClauseBrief],
    qa_id: int = -1,
) -> Iterator[Event]:
    from rag_core.errors import RagError

    t0 = time.time()
    cite_stats = {"cited": 0, "out_of_range": 0, "regenerated": False, "hallucination": False}

    if not clauses:
        yield from _refusal_events(t0, qa_id, [], 0.0)
        return

    max_score = max((c.score for c in clauses), default=0.0)
    if max_score < CONFIDENCE_THRESHOLD:
        yield from _refusal_events(t0, qa_id, _sources_data(clauses[:3]), max_score)
        return

    prompt = _build_prompt(question, clauses)

    def _collect(p: str) -> str:
        chunks: list[str] = []
        for text_chunk in _call_llm(p):
            chunks.append(text_chunk)
        return "".join(chunks)

    try:
        answer = _collect(prompt)
    except Exception as e:
        raise RagError("GENERATE_FAILED", f"LLM 生成失败: {e}") from e

    # ---- 引用校验防线 ----
    check = _validate_citations(answer, clauses)
    cite_stats["cited"] = len(_extract_citations(answer))
    if not check["ok"]:
        cite_stats["out_of_range"] = len(check["out_of_range"])
        logger.warning("引用越界，剔除引用句后重生成一次: bad_idx=%s q=%s",
                       check["out_of_range"], question[:50])
        cite_stats["regenerated"] = True
        retry_prompt = (
            prompt + "\n\n上一次回答引用了上下文中不存在的条文，这是不允许的。"
            "请只依据给定条文重新回答，且引用编号不得超过给定范围：\n"
            f"可保留的作答参考：{check['cleaned']}\n\n请重新回答："
        )
        try:
            answer = _collect(retry_prompt)
        except Exception as e:
            raise RagError("GENERATE_FAILED", f"LLM 生成失败: {e}") from e
        check = _validate_citations(answer, clauses)
        if not check["ok"]:
            # 仍越界 → 降级为拒答，记日志统计幻觉次数
            cite_stats["hallucination"] = True
            logger.error("重生成后仍引用越界，降级拒答并记为幻觉: bad_idx=%s q=%s",
                         check["out_of_range"], question[:50])
            yield from _refusal_events(t0, qa_id, _sources_data(clauses[:3]), max_score)
            return

    cite_stats["cited"] = len(_extract_citations(answer))
    logger.info("引用校验通过 q=%s stats=%s", question[:50], json.dumps(cite_stats, ensure_ascii=False))

    yield Event("delta", {"text": answer})
    yield Event("sources", {"sources": _sources_data(clauses)})
    yield Event("score", {"score": max_score})
    yield Event("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})
