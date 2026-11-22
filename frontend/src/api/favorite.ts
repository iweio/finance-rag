/**
 * /api/v1/favorite —— 收藏
 * POST 的 clause_id 与 qa_id 二选一；重复收藏幂等返回同一个 favorite_id。
 */
import { ApiError } from '@/utils/error'

import { request } from './client'
import type {
  DeleteResult,
  FavoriteCreate,
  FavoriteCreateResult,
  FavoritePage,
  FavoriteQuery,
} from './types'

export function getFavorites(query: FavoriteQuery = {}): Promise<FavoritePage> {
  return request<FavoritePage>({
    url: '/favorite',
    method: 'get',
    params: {
      page: query.page ?? 1,
      size: query.size ?? 10,
      ...(query.clause_id ? { clause_id: query.clause_id } : {}),
    },
  })
}

export function createFavorite(payload: FavoriteCreate): Promise<FavoriteCreateResult> {
  const hasClause = typeof payload.clause_id === 'string' && payload.clause_id.length > 0
  const hasQa = typeof payload.qa_id === 'number' && Number.isFinite(payload.qa_id)
  if (!hasClause && !hasQa) {
    throw new ApiError(40001, 'clause_id 与 qa_id 至少给一个')
  }
  return request<FavoriteCreateResult>({
    url: '/favorite',
    method: 'post',
    data: {
      clause_id: hasClause ? payload.clause_id : null,
      qa_id: hasQa ? payload.qa_id : null,
    },
  })
}

export function deleteFavorite(favoriteId: number): Promise<DeleteResult> {
  return request<DeleteResult>({
    url: '/favorite',
    method: 'delete',
    params: { favorite_id: favoriteId },
  })
}
