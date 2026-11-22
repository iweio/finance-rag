/**
 * 统一错误类型与错误码文案。
 * 错误码与 `契约/API.md` §0.4 + 认证扩展保持一致。
 */

export const ERROR_TEXT: Record<number, string> = {
  40001: '参数不合法，请检查输入',
  40101: '未登录或登录已过期',
  40301: '没有权限访问该资源',
  40401: '条文不存在',
  40402: '会话不存在',
  40901: '用户名已存在',
  50001: '检索失败，请稍后重试',
  50002: '生成失败，请稍后重试',
  50003: '数据库错误，请稍后重试',
  50301: '依赖服务不可用（Milvus / MySQL）',
}

export class ApiError extends Error {
  readonly code: number
  readonly payload: unknown

  constructor(code: number, message: string, payload: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.payload = payload
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

export function errorCode(value: unknown): number | null {
  return isApiError(value) ? value.code : null
}

/** 把任意异常转成可展示的中文文案；ApiError 的 message 优先（后端给的是人话） */
export function errorMessage(value: unknown, fallback = '请求失败，请稍后重试'): string {
  if (isApiError(value)) {
    if (value.message) return value.message
    return ERROR_TEXT[value.code] ?? fallback
  }
  if (value instanceof Error && value.message) return value.message
  return fallback
}

/** fetch（chat 流式）失败时，把响应体解析成 ApiError */
export async function responseToApiError(res: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await res.clone().json()
  } catch {
    body = null
  }
  const envelope = body as { code?: unknown; message?: unknown } | null
  const code =
    envelope && typeof envelope.code === 'number'
      ? envelope.code
      : res.status === 401
        ? 40101
        : res.status === 403
          ? 40301
          : res.status === 404
            ? 40401
            : res.status === 503
              ? 50301
              : 50001
  const message =
    envelope && typeof envelope.message === 'string' && envelope.message
      ? envelope.message
      : (ERROR_TEXT[code] ?? `请求失败（HTTP ${res.status}）`)
  return new ApiError(code, message, body)
}
