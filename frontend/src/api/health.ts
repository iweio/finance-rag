/**
 * GET /api/v1/health —— 依赖健康 + 知识库行数。
 *
 * 注意：依赖不通时后端返回 **HTTP 503**，但响应体仍是统一包（code 50301 + data）。
 * 这里用 validateStatus 放行非 2xx，把 data 交给页面渲染"哪一项 down"，
 * 而不是当成网络错误丢掉信息。
 */
import { ApiError } from '@/utils/error'

import { http } from './client'
import type { ApiEnvelope, Health } from './types'

export async function getHealth(): Promise<Health> {
  const response = await http.request<ApiEnvelope<Health>>({
    url: '/health',
    method: 'get',
    validateStatus: () => true,
    timeout: 8000,
  })
  const envelope = response.data
  if (envelope && typeof envelope === 'object' && 'data' in envelope && envelope.data) {
    return envelope.data
  }
  const code = (envelope as { code?: number } | null)?.code ?? 50301
  const message = (envelope as { message?: string } | null)?.message ?? '依赖服务不可用'
  throw new ApiError(code, message, envelope)
}
