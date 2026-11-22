<template>
  <div class="search-page">
    <header class="page-head">
      <div>
        <h1>条文检索</h1>
        <p>混合检索（向量 + BM25 + RRF + rerank），结果是条文级；`total` 是候选数，上限 200。</p>
      </div>
    </header>

    <div class="search-panel">
      <el-input
        v-model="keyword"
        size="large"
        clearable
        placeholder="输入关键词，如：资本充足率、北京证券交易所"
        @keyup.enter="submit"
      />
      <el-button type="primary" size="large" :loading="loading" @click="submit">检索</el-button>
    </div>

    <div v-if="!searched" class="search-examples">
      <span>试试：</span>
      <el-tag
        v-for="item in exampleKeywords"
        :key="item"
        class="search-examples__tag"
        effect="plain"
        @click="useExample(item)"
      >
        {{ item }}
      </el-tag>
    </div>

    <FilterBar v-model="filters" :dict="meta.dict" :loading="meta.loading" />

    <div v-if="searched" class="search-summary">
      <span>
        共 <strong>{{ totalLabel }}</strong> 条候选
        <template v-if="total >= 200">（已达上限，只展示前 200 条）</template>
      </span>
      <span v-if="activeCount > 0" class="muted">已应用 {{ activeCount }} 个筛选</span>
    </div>

    <LoadingSkeleton v-if="loading" :rows="6" variant="card" />

    <ErrorBanner v-else-if="error" :message="error" :code="errorCode">
      <template #action>
        <el-button size="small" @click="run(1)">重试</el-button>
      </template>
    </ErrorBanner>

    <EmptyState
      v-else-if="searched && items.length === 0"
      title="当前条件下没有命中"
      description="换个关键词，或清除筛选条件再试。"
      icon="search"
    >
      <template #action>
        <el-button @click="clearFilters">清除筛选</el-button>
      </template>
    </EmptyState>

    <ul v-else-if="items.length" class="hit-list">
      <li v-for="(item, index) in items" :key="`${item.clause_id}-${index}`" class="hit">
        <div class="hit__head">
          <router-link class="hit__doc" :to="`/clause/${encodeURIComponent(item.clause_id)}`">
            {{ item.doc_title }}
          </router-link>
          <el-tag size="small" type="info" effect="plain">{{ item.clause_no }}</el-tag>
          <span class="hit__score">{{ formatScore(item.score) }}</span>
        </div>

        <!-- highlight 是后端已转义的 HTML，这里再过一次 DOMPurify 白名单 -->
        <p class="hit__snippet" v-html="sanitizeHighlight(item.highlight || item.snippet)" />

        <div class="hit__foot">
          <el-tag size="small" :type="statusType(item.status)">{{ item.status || '—' }}</el-tag>
          <a
            v-if="item.source_url"
            :href="item.source_url"
            target="_blank"
            rel="noopener noreferrer"
            class="link"
          >
            查看原文
          </a>
          <span v-else class="muted">来源页未收录</span>
          <router-link class="hit__detail link" :to="`/clause/${encodeURIComponent(item.clause_id)}`">
            条文详情 →
          </router-link>
        </div>
      </li>
    </ul>

    <div v-if="pages > 1" class="search-pager">
      <el-pagination
        background
        layout="prev, pager, next"
        :page-size="size"
        :current-page="page"
        :total="cappedTotal"
        @current-change="go"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

import { api } from '@/api'
import type { Filters, SearchItem } from '@/api/types'
import { useFiltersStore } from '@/stores/filters'
import { useMetaStore } from '@/stores/meta'
import { errorCode as toCode, errorMessage } from '@/utils/error'
import { formatScore, formatTotal } from '@/utils/format'
import { sanitizeHighlight } from '@/utils/markdown'

import EmptyState from '@/components/EmptyState.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import FilterBar from '@/components/FilterBar.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const meta = useMetaStore()
const filtersStore = useFiltersStore()

const filters = computed<Filters>({
  get: () => filtersStore.filters,
  set: (value: Filters) => filtersStore.setFilters(value),
})

const keyword = ref(filtersStore.keyword)
const items = ref<SearchItem[]>([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<number | null>(null)
const searched = ref(false)

const exampleKeywords = ['资本充足率', '北京证券交易所', '企业债券']

const cappedTotal = computed(() => Math.min(total.value, 200))
const totalLabel = computed(() => formatTotal(total.value))
const pages = computed(() => Math.max(1, Math.ceil(cappedTotal.value / size)))
const activeCount = computed(() => filtersStore.activeCount)

function statusType(status: string): 'success' | 'warning' | 'info' {
  if (status === '有效') return 'success'
  if (status === '尚未生效') return 'warning'
  return 'info'
}

async function run(targetPage: number): Promise<void> {
  const text = keyword.value.trim()
  if (!text) {
    ElMessage.warning('请输入关键词')
    return
  }
  loading.value = true
  error.value = null
  errorCode.value = null
  try {
    const result = await api.search({
      keyword: text,
      page: targetPage,
      size,
      filters: filtersStore.filters,
    })
    items.value = result.items
    total.value = result.total
    page.value = result.page || targetPage
    searched.value = true
  } catch (e: unknown) {
    items.value = []
    total.value = 0
    error.value = errorMessage(e)
    errorCode.value = toCode(e)
    searched.value = true
  } finally {
    loading.value = false
  }
}

function submit(): void {
  filtersStore.keyword = keyword.value.trim()
  page.value = 1
  void run(1)
}

function go(targetPage: number): void {
  if (targetPage < 1 || targetPage > pages.value) return
  page.value = targetPage
  void run(targetPage)
}

function clearFilters(): void {
  filtersStore.reset()
  if (keyword.value.trim()) void run(1)
}

function useExample(word: string): void {
  keyword.value = word
  submit()
}

watch(
  () => filtersStore.filters,
  () => {
    if (searched.value) void run(1)
  },
  { deep: true },
)

onMounted(() => {
  void meta.load()
  if (filtersStore.keyword) {
    keyword.value = filtersStore.keyword
    void run(1)
  }
})
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.page-head h1 {
  margin: 0;
  font-size: 17px;
  color: var(--rag-text);
}
.page-head p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--rag-text-3);
}
.search-panel {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.search-panel :deep(.el-input) {
  flex: 1;
}
.search-examples {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--rag-text-3);
}
.search-examples__tag {
  cursor: pointer;
}
.search-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 14px 0 10px;
  font-size: 13px;
  color: var(--rag-text-2);
}
.search-summary strong {
  color: var(--rag-text);
  font-variant-numeric: tabular-nums;
}
.hit-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.hit {
  padding: 12px 14px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.hit:hover {
  border-color: var(--rag-primary-200);
  box-shadow: var(--rag-shadow-sm);
}
.hit__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.hit__doc {
  font-weight: 600;
  font-size: 14px;
  color: var(--rag-text);
  text-decoration: none;
}
.hit__doc:hover {
  color: var(--rag-primary);
}
.hit__score {
  margin-left: auto;
  font-size: 12px;
  color: var(--rag-text-3);
  font-variant-numeric: tabular-nums;
}
.hit__snippet {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.8;
  color: var(--rag-text-2);
  word-break: break-word;
}
.hit__snippet :deep(em) {
  padding: 0 2px;
  border-radius: 3px;
  background: var(--rag-highlight);
  color: var(--rag-text);
  font-style: normal;
  font-weight: 600;
}
.hit__foot {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--rag-text-3);
}
.hit__detail {
  margin-left: auto;
}
.link {
  color: var(--rag-primary);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.search-pager {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}
@media (max-width: 768px) {
  .search-panel {
    flex-direction: column;
  }
}
</style>
