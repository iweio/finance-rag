/** 筛选字典：全局只拉一次，FilterBar / 页面共用 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import type { FilterDict } from '@/api/types'
import { errorMessage } from '@/utils/error'

export const useMetaStore = defineStore('meta', () => {
  const dict = ref<FilterDict | null>(null)
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    if (loading.value) return
    loading.value = true
    error.value = null
    try {
      dict.value = await api.getFilters()
      loaded.value = true
    } catch (e: unknown) {
      error.value = errorMessage(e, '筛选字典加载失败')
    } finally {
      loading.value = false
    }
  }

  return { dict, loading, loaded, error, load }
})
