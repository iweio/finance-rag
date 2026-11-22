/**
 * 收藏列表。收藏是登录后的能力：未登录时 store 清空、不请求。
 * 详情页的「收藏 / 已收藏」判断走 `isFavorite(clause_id)`。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import type { FavoriteCreateResult, FavoriteItem } from '@/api/types'
import { errorMessage } from '@/utils/error'

import { useAuthStore } from './auth'

const PAGE_SIZE = 50

export const useFavoriteStore = defineStore('favorite', () => {
  const items = ref<FavoriteItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)

  const clauseIds = computed<Set<string>>(
    () => new Set(items.value.map((item) => item.clause_id)),
  )

  function isFavorite(clauseId: string): boolean {
    return clauseIds.value.has(clauseId)
  }

  async function load(force = false): Promise<void> {
    const auth = useAuthStore()
    if (!auth.isLoggedIn) {
      items.value = []
      total.value = 0
      loaded.value = false
      return
    }
    if (loaded.value && !force) return

    loading.value = true
    error.value = null
    try {
      const page = await api.getFavorites({ page: 1, size: PAGE_SIZE })
      items.value = page.items
      total.value = page.total
      loaded.value = true
    } catch (e: unknown) {
      error.value = errorMessage(e, '收藏列表加载失败')
    } finally {
      loading.value = false
    }
  }

  async function add(clauseId: string): Promise<FavoriteCreateResult> {
    const result = await api.createFavorite({ clause_id: clauseId })
    await load(true)
    return result
  }

  async function addFromQa(qaId: number): Promise<FavoriteCreateResult> {
    const result = await api.createFavorite({ qa_id: qaId })
    await load(true)
    return result
  }

  async function remove(favoriteId: number): Promise<void> {
    await api.deleteFavorite(favoriteId)
    items.value = items.value.filter((item) => item.favorite_id !== favoriteId)
    total.value = Math.max(0, total.value - 1)
  }

  function reset(): void {
    items.value = []
    total.value = 0
    loaded.value = false
    error.value = null
  }

  return { items, total, loading, loaded, error, isFavorite, load, add, addFromQa, remove, reset }
})
