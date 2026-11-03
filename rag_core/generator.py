"""LLM 流式生成。

接收检索到的条文 + 用户问题，拼 Prompt 调 LLM，流式吐字。
SSE 事件顺序固定：delta* → sources → score → done。

无依据拒答：检索不到相关内容时输出拒答话术，sources 为空数组，score 低于阈值。
"""
from __future__ import annotations

import json
import os
import time
from typing import Iterator

from rag_core.models import ClauseBrief, Event

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "dashscope")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

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
        "2. 回答中引用条文时，注明出自哪部法规的第几条。\n"
        "3. 如果给定条文与问题无关或信息不足，请明确说明无法回答。\n"
        "4. 语言简洁准确，使用中文。\n\n"
        f"用户问题：{question}\n\n"
        f"给定条文：\n{context}\n\n"
        "请回答："
    )


def _call_dashscope_stream(prompt: str) -> Iterator[str]:
    try:
        import dashscope
        from dashscope import Generation
    except ImportError:
        raise RuntimeError(
            "缺 dashscope。pip install dashscope"
        )

    dashscope.api_key = LLM_API_KEY
    responses = Generation.call(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        result_format="message",
        stream=True,
        incremental_output=True,
    )
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
    client = OpenAI(api_key=LLM_API_KEY, base_url=base_url)
    stream = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def _call_llm(prompt: str) -> Iterator[str]:
    if LLM_PROVIDER == "dashscope":
        return _call_dashscope_stream(prompt)
    elif LLM_PROVIDER == "openai":
        return _call_openai_stream(prompt)
    else:
        raise RuntimeError(f"不支持的 LLM_PROVIDER: {LLM_PROVIDER}")


def generate_stream(
    question: str,
    clauses: list[ClauseBrief],
    qa_id: int = -1,
) -> Iterator[Event]:
    t0 = time.time()

    if not clauses:
        yield Event("delta", {"text": REFUSAL_TEXT})
        yield Event("sources", {"sources": []})
        yield Event("score", {"score": 0.0})
        yield Event("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})
        return

    max_score = max((c.score for c in clauses), default=0.0)
    if max_score < CONFIDENCE_THRESHOLD:
        yield Event("delta", {"text": REFUSAL_TEXT})
        yield Event("sources", {"sources": [c.__dict__ for c in clauses[:3]]})
        yield Event("score", {"score": max_score})
        yield Event("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})
        return

    prompt = _build_prompt(question, clauses)

    try:
        for text_chunk in _call_llm(prompt):
            yield Event("delta", {"text": text_chunk})
    except Exception as e:
        from rag_core.errors import RagError
        raise RagError("GENERATE_FAILED", f"LLM 生成失败: {e}") from e

    sources_data = []
    for c in clauses:
        sources_data.append({
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
        })

    yield Event("sources", {"sources": sources_data})
    yield Event("score", {"score": max_score})
    yield Event("done", {"qa_id": qa_id, "elapsed_ms": int((time.time() - t0) * 1000)})
