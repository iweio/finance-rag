/**
 * 检索条件。契约里的 filters 只有 domain / status / org 三个字段，
 * doc_type 与 topic_tags 只做字典展示（裁决 #1），所以这里不放它们。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { Filters } from '@/api/types'

export const useFiltersStore = defineStore('filters', () => {
  const filters = ref<Filters>({ domain: null, status: null, org: null })
  const keyword = ref('')

  const activeCount = computed<number>(() =>
    [filters.value.domain, filters.value.status, filters.value.org].filter(
      (value) => typeof value === 'string' && value.length > 0,
    ).length,
  )

  function setFilters(next: Filters): void {
    filters.value = {
      domain: next.domain ?? null,
      status: next.status ?? null,
      org: next.org ?? null,
    }
  }

  function reset(): void {
    filters.value = { domain: null, status: null, org: null }
  }

  return { filters, keyword, activeCount, setFilters, reset }
})
