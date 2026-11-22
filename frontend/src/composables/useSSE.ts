/**
 * SSE 解析 + 问答流的状态机。
 *
 * 为什么不用 `EventSource`：`/api/v1/chat` 是 **POST**，要带 body 和 Authorization，
 * 而 `EventSource` 只支持 GET、不能设 header。所以必须 `fetch` + `ReadableStream` 手写解析。
 *
 * 解析规则（对应计划 §4.5）：
 * - 按空行 `\n\n` 切事件块；
 * - `event:` 缺省为 `message`；
 * - `data:` 可能多行，按 `\n` 拼起来再 JSON.parse；
 * - 以 `:` 开头的是注释/心跳，直接丢；
 * - `done` 只认第一个（后端历史上可能发两次，防御性保留）；
 * - `error` 事件即收尾，不当网络异常处理。
 */
import { getCurrentScope, onScopeDispose, reactive } from 'vue'

import type { ChatEvent, ClauseBrief } from '@/api/types'
import { errorCode, errorMessage, isApiError } from '@/utils/error'

export interface SseMessage {
  event: string
  data: string
}

/** 解析单个 SSE 事件块 */
export function parseSseBlock(block: string): SseMessage | null {
  let event = 'message'
  const dataLines: string[] = []

  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }

  if (dataLines.length === 0 && event === 'message') return null
  return { event, data: dataLines.join('\n') }
}

/** 把 fetch 的响应体流解析成 SSE 事件 */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseMessage> {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')

      let index = buffer.indexOf('\n\n')
      while (index !== -1) {
        const block = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        const message = parseSseBlock(block)
        if (message) yield message
        index = buffer.indexOf('\n\n')
      }
    }

    buffer += decoder.decode()
    if (buffer.trim()) {
      const message = parseSseBlock(buffer)
      if (message) yield message
    }
  } finally {
    reader.releaseLock()
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

/** SSE 消息 → 强类型 ChatEvent；未知事件返回 null（记 warning 后忽略） */
export function parseChatEvent(message: SseMessage): ChatEvent | null {
  let payload: unknown
  try {
    payload = message.data ? JSON.parse(message.data) : {}
  } catch {
    console.warn('[sse] 无法解析 data:', message.event, message.data.slice(0, 120))
    return null
  }
  const data = asRecord(payload) ?? {}

  switch (message.event) {
    case 'delta':
      return { type: 'delta', text: typeof data.text === 'string' ? data.text : '' }
    case 'sources':
      return {
        type: 'sources',
        sources: Array.isArray(data.sources) ? (data.sources as ClauseBrief[]) : [],
      }
    case 'score':
      return { type: 'score', score: typeof data.score === 'number' ? data.score : 0 }
    case 'done':
      return {
        type: 'done',
        qa_id: typeof data.qa_id === 'number' ? data.qa_id : -1,
        elapsed_ms: typeof data.elapsed_ms === 'number' ? data.elapsed_ms : 0,
      }
    case 'error':
      return {
        type: 'error',
        code: typeof data.code === 'number' ? data.code : 50001,
        message: typeof data.message === 'string' ? data.message : '生成失败',
      }
    default:
      console.warn('[sse] 未知事件，已忽略:', message.event)
      return null
  }
}

export type ChatStreamStatus = 'idle' | 'streaming' | 'done' | 'error' | 'aborted'

export interface ChatStreamState {
  answer: string
  sources: ClauseBrief[]
  score: number | null
  qaId: number | null
  elapsedMs: number | null
  status: ChatStreamStatus
  error: string | null
  errorCode: number | null
}

function isAbortError(error: unknown): boolean {
  return !!error && typeof error === 'object' && (error as { name?: string }).name === 'AbortError'
}

/**
 * 问答流状态机：把 AsyncGenerator<ChatEvent> 变成一组可绑定的状态。
 *
 * `open` 由调用方给（真实实现是 api.chatStream，Mock 实现是 mock 的生成器），
 * 所以本文件不 import api，避免 composable ↔ api 的循环依赖。
 */
export function useChatStream() {
  const state = reactive<ChatStreamState>({
    answer: '',
    sources: [],
    score: null,
    qaId: null,
    elapsedMs: null,
    status: 'idle',
    error: null,
    errorCode: null,
  })

  let controller: AbortController | null = null
  let pending = ''
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  function flush(): void {
    if (!pending) return
    state.answer += pending
    pending = ''
  }

  // 增量按 40ms 合并一次再进响应式状态：中文长答案逐字触发 v-html 重渲染会掉帧
  function scheduleFlush(): void {
    if (flushTimer !== null) return
    flushTimer = setTimeout(() => {
      flushTimer = null
      flush()
    }, 40)
  }

  function reset(): void {
    if (flushTimer !== null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    pending = ''
    state.answer = ''
    state.sources = []
    state.score = null
    state.qaId = null
    state.elapsedMs = null
    state.status = 'idle'
    state.error = null
    state.errorCode = null
  }

  function stop(): void {
    controller?.abort()
  }

  async function start(open: (signal: AbortSignal) => AsyncGenerator<ChatEvent>): Promise<void> {
    if (state.status === 'streaming') stop()
    reset()

    const current = new AbortController()
    controller = current
    state.status = 'streaming'
    let sawDone = false

    try {
      for await (const event of open(current.signal)) {
        if (current.signal.aborted) break

        if (event.type === 'delta') {
          pending += event.text
          scheduleFlush()
        } else if (event.type === 'sources') {
          flush()
          state.sources = event.sources ?? []
        } else if (event.type === 'score') {
          flush()
          state.score = event.score
        } else if (event.type === 'done') {
          if (sawDone) continue
          sawDone = true
          state.qaId = event.qa_id
          state.elapsedMs = event.elapsed_ms
          break
        } else if (event.type === 'error') {
          flush()
          state.error = event.message
          state.errorCode = event.code
          state.status = 'error'
          return
        }
      }

      if (state.status === 'streaming') {
        state.status = current.signal.aborted ? 'aborted' : 'done'
      }
    } catch (error: unknown) {
      flush()
      if (isAbortError(error)) {
        state.status = 'aborted'
      } else {
        state.error = errorMessage(error)
        state.errorCode = errorCode(error)
        // 后端以 HTTP 错误码返回时，ApiError 里可能带着 envelope，仍按 error 处理
        if (isApiError(error) && !state.error) state.error = '请求失败'
        state.status = 'error'
      }
    } finally {
      flush()
      if (flushTimer !== null) {
        clearTimeout(flushTimer)
        flushTimer = null
      }
      if (controller === current) controller = null
    }
  }

  if (getCurrentScope()) {
    onScopeDispose(() => stop())
  }

  return { state, start, stop, reset }
}

/** 命名别名，页面既可以叫 useSSE 也可以叫 useChatStream */
export function useSSE() {
  return useChatStream()
}
