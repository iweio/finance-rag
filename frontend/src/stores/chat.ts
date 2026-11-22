/**
 * 问答页的会话内状态：一轮问答 = 一条 ChatTurn。
 * 放进 store 是为了「切到条文详情再回来」不丢当前对话（页面组件会随路由卸载）。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

import type { ClauseBrief } from '@/api/types'

export type ChatTurnStatus = 'streaming' | 'done' | 'error' | 'aborted'

export interface ChatTurn {
  id: string
  question: string
  answer: string
  sources: ClauseBrief[]
  score: number | null
  qa_id: number | null
  elapsed_ms: number | null
  status: ChatTurnStatus
  error: string | null
  error_code: number | null
}

let sequence = 0

function nextId(): string {
  sequence += 1
  return `turn_${Date.now().toString(36)}_${sequence}`
}

export const useChatStore = defineStore('chat', () => {
  const turns = ref<ChatTurn[]>([])

  function createTurn(question: string): string {
    const id = nextId()
    turns.value.push({
      id,
      question,
      answer: '',
      sources: [],
      score: null,
      qa_id: null,
      elapsed_ms: null,
      status: 'streaming',
      error: null,
      error_code: null,
    })
    return id
  }

  function patchTurn(id: string, patch: Partial<ChatTurn>): void {
    const index = turns.value.findIndex((turn) => turn.id === id)
    if (index < 0) return
    turns.value[index] = { ...turns.value[index], ...patch }
  }

  function clear(): void {
    turns.value = []
  }

  function remove(id: string): void {
    turns.value = turns.value.filter((turn) => turn.id !== id)
  }

  return { turns, createTurn, patchTurn, clear, remove }
})
