/**
 * Mock 的问答流：与真实实现同签名（AsyncGenerator<ChatEvent>），页面不感知。
 *
 * 数据全部来自 fixtures.json 的真实条文，不手编法条内容。
 * 触发词（仅 mock）：
 *   - 问题含「拒答」→ 拒答话术 + sources=[] + score=0.12
 *   - 问题含「错误」→ 先吐 2 段 delta，再发 error 事件（模拟 LLM 失败）
 */
import type { ChatEvent, ChatRequest, ClauseBrief, ClauseDetail, RelatedClause } from '@/api/types'

import fixtures from './fixtures.json'

interface StreamFixtures {
  details: ClauseDetail[]
  related: RelatedClause[]
}

const F = fixtures as unknown as StreamFixtures

export const REFUSAL_TEXT =
  '抱歉，当前知识库中未找到与您问题直接相关的制度条文，无法给出准确回答。建议调整问题措辞，或咨询专业法律人员。'

export function abortError(): DOMException {
  return new DOMException('Aborted', 'AbortError')
}

/** 可被 AbortSignal 中断的 sleep */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/** 把答案切成 8~15 段，供「分句 delta」演示（计划 §9 #4） */
export function splitAnswer(text: string, min = 8, max = 15): string[] {
  const normalized = text.replace(/\r\n/g, '\n')
  let parts = normalized
    .split(/(?<=[。；：！？\n])/)
    .map((piece) => piece.trim())
    .filter(Boolean)

  // 段数太少：继续按逗号拆最长的那一段
  let guard = 0
  while (parts.length < min && guard < 40) {
    guard += 1
    let index = -1
    let longest = 0
    parts.forEach((piece, i) => {
      if (piece.length > longest) {
        longest = piece.length
        index = i
      }
    })
    if (index < 0 || longest < 20) break
    const sub = parts[index].split(/(?<=[，,])/).filter(Boolean)
    if (sub.length < 2) break
    parts.splice(index, 1, ...sub)
  }

  // 段数太多：相邻合并
  if (parts.length > max) {
    const step = Math.ceil(parts.length / max)
    const merged: string[] = []
    for (let i = 0; i < parts.length; i += step) {
      merged.push(parts.slice(i, i + step).join(''))
    }
    parts = merged
  }

  return parts.length > 0 ? parts : [text]
}

function toBrief(detail: ClauseDetail, score: number): ClauseBrief {
  const snippet =
    detail.text.length > 180 ? `${detail.text.slice(0, 180)}…` : detail.text
  return {
    clause_id: detail.clause_id,
    doc_id: detail.doc_id,
    doc_title: detail.doc_title,
    clause_no: detail.clause_no,
    clause_index: detail.clause_index,
    chapter: detail.chapter,
    snippet,
    score,
    status: detail.status,
    effective_date: detail.effective_date,
    source_url: detail.source_url,
  }
}

function toBriefFromRelated(item: RelatedClause): ClauseBrief {
  return {
    clause_id: item.clause_id,
    doc_id: item.doc_id,
    doc_title: item.doc_title,
    clause_no: item.clause_no,
    clause_index: item.clause_index,
    chapter: item.chapter,
    snippet: item.snippet,
    score: item.score,
    status: item.status,
    effective_date: item.effective_date,
    source_url: item.source_url,
  }
}

const KEYWORD_ROUTES: Array<{ pattern: RegExp; clauseId: string }> = [
  { pattern: /资本|充足率|风险加权|监管指标/, clauseId: 'NFRA-016-A26' },
  { pattern: /贷款|资产负债|商业银行法/, clauseId: 'NPC-006-A39' },
  { pattern: /征信|信用评级/, clauseId: 'GOV-002-A2' },
  { pattern: /上市|交易所|北交所|股票/, clauseId: 'BSE-001-1.1' },
  { pattern: /保险/, clauseId: 'GOV-012-A1' },
  { pattern: /外汇|跨境|债券/, clauseId: 'PBC-018-A1' },
  { pattern: /非法集资|集资/, clauseId: 'GOV-009-A2' },
]

function pickDetail(question: string): ClauseDetail {
  for (const route of KEYWORD_ROUTES) {
    if (route.pattern.test(question)) {
      const hit = F.details.find((item) => item.clause_id === route.clauseId)
      if (hit) return hit
    }
  }
  return F.details[0]
}

let qaSequence = 10241

export async function* mockChatStream(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const startedAt = Date.now()
  const question = req.question || ''
  await sleep(180, signal)

  // 1) 拒答用例
  if (question.includes('拒答')) {
    for (const piece of splitAnswer(REFUSAL_TEXT, 2, 4)) {
      await sleep(80, signal)
      yield { type: 'delta', text: piece }
    }
    yield { type: 'sources', sources: [] }
    yield { type: 'score', score: 0.12 }
    yield { type: 'done', qa_id: qaSequence++, elapsed_ms: Date.now() - startedAt }
    return
  }

  // 2) 错误用例
  if (question.includes('错误')) {
    for (const piece of ['正在检索相关条文……', '已找到候选条文，正在生成答案……']) {
      await sleep(80, signal)
      yield { type: 'delta', text: piece }
    }
    yield {
      type: 'error',
      code: 50002,
      message: '生成失败（mock：问题含「错误」触发）。请稍后重试或换个问法。',
    }
    return
  }

  // 3) 正常用例：用真实条文拼答案，按句吐 delta
  const detail = pickDetail(question)
  const answer = `根据《${detail.doc_title}》${detail.clause_no}的规定：\n\n${detail.text}`
  const pieces = splitAnswer(answer)

  for (const piece of pieces) {
    await sleep(80, signal)
    yield { type: 'delta', text: piece }
  }

  const relatedForDoc = F.related
    .filter((item) => item.doc_id === detail.doc_id && item.clause_id !== detail.clause_id)
    .slice(0, 2)
    .map(toBriefFromRelated)

  const primary = toBrief(detail, 0.92)
  yield { type: 'sources', sources: [primary, ...relatedForDoc] }
  yield { type: 'score', score: 0.92 }
  yield { type: 'done', qa_id: qaSequence++, elapsed_ms: Date.now() - startedAt }
}
