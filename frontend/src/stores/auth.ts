/**
 * 登录态：token 存 localStorage `rag_token`，user 存 `rag_user`。
 * 前端只 base64 解 JWT 的 `exp` 判过期，**不校验签名**（校验在服务端）。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import type { AuthToken, AuthUser } from '@/api/types'
import { clearToken, getToken, setToken } from '@/api/client'

const USER_KEY = 'rag_user'

function decodeExp(token: string): number | null {
  const part = token.split('.')[1]
  if (!part) return null
  const normalized = part.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
  try {
    const binary = atob(padded)
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as { exp?: unknown }
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch {
    return null
  }
}

function loadUser(): AuthUser | null {
  try {
    const raw = window.localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AuthUser>
    if (typeof parsed.user_id === 'number' && typeof parsed.username === 'string') {
      return { user_id: parsed.user_id, username: parsed.username }
    }
  } catch {
    /* ignore */
  }
  return null
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken())
  const user = ref<AuthUser | null>(loadUser())

  const expiresAt = computed<number | null>(() => {
    if (!token.value) return null
    const exp = decodeExp(token.value)
    return exp === null ? null : exp * 1000
  })

  const isLoggedIn = computed<boolean>(() => {
    if (!token.value) return false
    const exp = expiresAt.value
    // 解不出 exp 时不主动判定过期，交给服务端 40101
    return exp === null ? true : exp > Date.now()
  })

  const displayName = computed<string>(() => user.value?.username ?? '')

  function persist(data: AuthToken): void {
    token.value = data.token
    user.value = { user_id: data.user_id, username: data.username }
    setToken(data.token)
    try {
      window.localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } catch {
      /* ignore */
    }
  }

  async function login(username: string, password: string): Promise<AuthToken> {
    const data = await api.login({ username, password })
    persist(data)
    return data
  }

  async function register(username: string, password: string): Promise<AuthUser> {
    return api.register({ username, password })
  }

  function clearLocal(): void {
    token.value = null
    user.value = null
    clearToken()
    try {
      window.localStorage.removeItem(USER_KEY)
    } catch {
      /* ignore */
    }
  }

  function logout(): void {
    clearLocal()
  }

  // 启动时把已过期的 token 清掉，避免带着废 token 满屏 401
  if (token.value && expiresAt.value !== null && expiresAt.value <= Date.now()) {
    clearLocal()
  }

  return { token, user, isLoggedIn, expiresAt, displayName, login, register, logout, clearLocal }
})
