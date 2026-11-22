/** GET /api/v1/clause/{clause_id} 与 /clause/{clause_id}/related */
import { request } from './client'
import type { ClauseDetail, RelatedResult } from './types'

export function getClause(clauseId: string): Promise<ClauseDetail> {
  return request<ClauseDetail>({
    url: `/clause/${encodeURIComponent(clauseId)}`,
    method: 'get',
  })
}

export function getRelated(clauseId: string, topK = 5): Promise<RelatedResult> {
  return request<RelatedResult>({
    url: `/clause/${encodeURIComponent(clauseId)}/related`,
    method: 'get',
    params: { top_k: topK },
  })
}
