/** GET /api/v1/meta/filters —— 筛选字典（5 个轴 + date_range） */
import { request } from './client'
import type { FilterDict } from './types'

export function getFilters(): Promise<FilterDict> {
  return request<FilterDict>({ url: '/meta/filters', method: 'get' })
}
