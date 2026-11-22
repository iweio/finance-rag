/**
 * POST /api/v1/chat —— POST + SSE，不能用 EventSource。
 * 真实实现：fetch → parseSseStream → ChatEvent；与 Mock 实现同签名（AsyncGenerator）。
 */
import { parseChatEvent, parseSseStream } from '@/composables/useSSE'
import { ApiError, responseToApiError } from '@/utils/error'

import { API_BASE, getToken } from './client'
import type { ChatEvent, ChatRequest } from './types'

export async function* chatStream(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify(req),
      signal,
    })
  } catch (error: unknown) {
    const name = (error as { name?: string } | null)?.name
    if (name === 'AbortError' || signal?.aborted) throw error
    throw new ApiError(50001, '无法连接后端服务，请确认后端已启动')
  }

  if (!response.ok) throw await responseToApiError(response)
  if (!response.body) throw new ApiError(50001, '服务端未返回流式响应')

  for await (const message of parseSseStream(response.body)) {
    const event = parseChatEvent(message)
    if (!event) continue
    yield event
    // done / error 都是收尾信号：后端若还继续发，前端不再消费
    if (event.type === 'done' || event.type === 'error') return
  }
}
