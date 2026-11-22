/** POST /api/v1/search —— 混合检索（向量 + BM25 + RRF） */
import { cleanFilters, request } from './client'
import type { SearchRequest, SearchResult } from './types'

export function search(req: SearchRequest): Promise<SearchResult> {
  return request<SearchResult>({
    url: '/search',
    method: 'post',
    data: {
      keyword: req.keyword,
      page: req.page,
      size: req.size,
      filters: cleanFilters(req.filters),
    },
  })
}
