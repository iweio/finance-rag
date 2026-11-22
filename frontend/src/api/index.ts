/** 真实实现与 Mock 实现的切换点 + 类型出口。页面只 import 这个文件。 */
import type { RagApi } from './types'

import * as authApi from './auth'
import { chatStream } from './chat'
import { getClause, getRelated } from './clause'
import { createFavorite, deleteFavorite, getFavorites } from './favorite'
import { graphStats, graphSubgraph } from './graph'
import { getHealth } from './health'
import { deleteHistory, getHistory } from './history'
import { getFilters } from './meta'
import { mockApi } from './mock'
import { search } from './search'

const USE_MOCK = String(import.meta.env.VITE_USE_MOCK ?? '')
  .trim()
  .toLowerCase() === 'true'

const realApi: RagApi = {
  login: authApi.login,
  register: authApi.register,
  chatStream,
  search,
  getClause,
  getRelated,
  getFilters,
  getHistory,
  deleteHistory,
  getFavorites,
  createFavorite,
  deleteFavorite,
  getHealth,
  graphStats,
  graphSubgraph,
}

/** 当前是否 Mock 模式（页面用它显示 "Mock" 角标，避免被当成真机数据） */
export const IS_MOCK = USE_MOCK

export const api: RagApi = USE_MOCK ? mockApi : realApi

export type * from './types'
export { ApiError, errorMessage, isApiError } from '@/utils/error'
export { API_BASE } from './client'
