/**
 * 会话 ID：契约要求由前端生成（`sess_` + 12 位 hex），存 localStorage 做单例。
 * 登录后**不重建**：历史归属靠后端 session.user_id，换 id 会让未登录时的历史丢失。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

const SESSION_KEY = 'rag_session_id'

function randomHex(byteLength: number): string {
  const bytes = new Uint8Array(byteLength)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < byteLength; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function loadOrCreate(): string {
  try {
    const cached = window.localStorage.getItem(SESSION_KEY)
    if (cached) return cached
  } catch {
    /* ignore */
  }
  const created = `sess_${randomHex(6)}`
  try {
    window.localStorage.setItem(SESSION_KEY, created)
  } catch {
    /* ignore */
  }
  return created
}

export const useSessionStore = defineStore('session', () => {
  const sessionId = ref<string>(loadOrCreate())
  return { sessionId }
})
