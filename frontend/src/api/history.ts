/**
 * /api/v1/history —— 问答历史
 * DELETE 必须且只能带 session_id 或 qa_id 之一（都传/都不传 → 40001）。
 */
import { ApiError } from '@/utils/error'

import { request } from './client'
import type { DeleteHistoryParams, DeleteResult, HistoryPage, HistoryQuery } from './types'

export function getHistory(query: HistoryQuery): Promise<HistoryPage> {
  return request<HistoryPage>({
    url: '/history',
    method: 'get',
    params: {
      session_id: query.session_id,
      page: query.page ?? 1,
      size: query.size ?? 10,
    },
  })
}

export function deleteHistory(params: DeleteHistoryParams): Promise<DeleteResult> {
  const hasSession = typeof params.session_id === 'string' && params.session_id.length > 0
  const hasQa = typeof params.qa_id === 'number' && Number.isFinite(params.qa_id)
  if (hasSession === hasQa) {
    throw new ApiError(40001, 'session_id 与 qa_id 必须且只能传一个')
  }
  return request<DeleteResult>({
    url: '/history',
    method: 'delete',
    params: hasSession ? { session_id: params.session_id } : { qa_id: params.qa_id },
  })
}
