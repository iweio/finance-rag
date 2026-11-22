/**
 * axios 实例 + token 存取 + 统一错误处理。
 *
 * 约定：
 * - 请求拦截器自动带 `Authorization: Bearer <token>`（token 存 localStorage `rag_token`）；
 * - `request<T>()` 负责拆统一响应包 `{code,message,data}`，`code !== 0` 抛 `ApiError`；
 * - `40101` 清 token 并回调 `onUnauthorized`（main.ts 注册，跳 `/login?redirect=`）；
 * - **chat 不走 axios**，见 `api/chat.ts`（POST + SSE，必须 fetch）。
 */
import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'

import { ApiError, ERROR_TEXT } from '@/utils/error'
import type { ApiEnvelope, Filters } from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/+$/, '')

const TOKEN_KEY = 'rag_token'

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略 */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

type UnauthorizedHandler = (redirect: string) => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由 main.ts 注册：清 Pinia 里的登录态 + 路由跳转 */
export function onUnauthorized(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

export function handleUnauthorized(): void {
  clearToken()
  if (typeof window === 'undefined') return
  const redirect = `${window.location.pathname}${window.location.search}`
  if (unauthorizedHandler) {
    unauthorizedHandler(redirect)
    return
  }
  // 兜底：没有注册 handler 时硬跳转
  if (window.location.pathname !== '/login') {
    window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
  }
}

/** HTTP 状态码兜底映射（响应体不是统一包时用） */
function statusToCode(status: number): number {
  if (status === 400) return 40001
  if (status === 401) return 40101
  if (status === 403) return 40301
  if (status === 404) return 40401
  if (status === 409) return 40901
  if (status === 503) return 50301
  return 50001
}

export const http = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (!axios.isAxiosError(error)) return Promise.reject(error)

    const response = error.response
    if (response) {
      const body = response.data as Partial<ApiEnvelope<unknown>> | null | undefined
      const code = typeof body?.code === 'number' ? body.code : statusToCode(response.status)
      const message =
        typeof body?.message === 'string' && body.message
          ? body.message
          : (ERROR_TEXT[code] ?? error.message)
      if (code === 40101) handleUnauthorized()
      return Promise.reject(new ApiError(code, message, body ?? null))
    }

    const message =
      error.code === 'ECONNABORTED'
        ? '请求超时，请稍后重试'
        : '无法连接后端服务，请确认后端已启动（或打开 Mock 模式）'
    return Promise.reject(new ApiError(50001, message))
  },
)

/** 发请求并拆包，成功返回 `data` 泛型 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response: AxiosResponse<ApiEnvelope<T>> = await http.request<ApiEnvelope<T>>(config)
  const envelope = response.data

  // 非统一包（理论上不该出现）直接原样返回，避免把数据吞掉
  if (!envelope || typeof envelope !== 'object' || typeof envelope.code !== 'number') {
    return envelope as unknown as T
  }
  if (envelope.code === 0) return envelope.data

  if (envelope.code === 40101) handleUnauthorized()
  throw new ApiError(
    envelope.code,
    envelope.message || ERROR_TEXT[envelope.code] || '请求失败',
    envelope,
  )
}

/**
 * 只保留契约里的 3 个筛选字段，空值丢掉。
 * 不传 `topic_tags` / `doc_type`：契约的 filters 没这两个字段（裁决 #1）。
 */
export function cleanFilters(filters?: Filters | null): Filters | undefined {
  if (!filters) return undefined
  const out: Filters = {}
  if (filters.domain) out.domain = filters.domain
  if (filters.status) out.status = filters.status
  if (filters.org) out.org = filters.org
  return Object.keys(out).length > 0 ? out : undefined
}
