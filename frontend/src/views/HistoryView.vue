<template>
  <div class="history-page">
    <header class="page-head">
      <div>
        <h1>问答历史</h1>
        <p>当前账号下的问答记录（需登录）。会话 ID：{{ session.sessionId }}</p>
      </div>
      <el-button v-if="items.length" type="danger" plain @click="clearAll">清空全部</el-button>
    </header>

    <LoadingSkeleton v-if="loading" :rows="6" variant="card" />

    <ErrorBanner v-else-if="error" :message="error" :code="errorCode" title="历史加载失败">
      <template #action>
        <el-button size="small" @click="load(page)">重试</el-button>
      </template>
    </ErrorBanner>

    <EmptyState
      v-else-if="items.length === 0"
      title="还没有问答记录"
      description="去问答页提一个问题，这里就会出现记录。"
    >
      <template #action>
        <el-button type="primary" @click="router.push('/chat')">去问答</el-button>
      </template>
    </EmptyState>

    <div v-else class="history-list">
      <article v-for="item in items" :key="item.qa_id" class="history-item">
        <header class="history-item__head">
          <div class="history-item__question">{{ item.question }}</div>
          <div class="history-item__meta">
            <ConfidenceBadge :score="item.score" />
            <span class="muted">{{ formatDateTime(item.created_at) }}</span>
            <el-button text type="danger" size="small" @click="removeOne(item)">删除</el-button>
          </div>
        </header>

        <el-collapse class="history-item__collapse">
          <el-collapse-item title="查看答案与引用" :name="item.qa_id">
            <MarkdownView :content="item.answer" />
            <div v-if="item.sources && item.sources.length" class="history-item__sources">
              <span class="muted">引用：</span>
              <router-link
                v-for="source in item.sources"
                :key="source.clause_id"
                class="history-item__source"
                :to="`/clause/${encodeURIComponent(source.clause_id)}`"
              >
                {{ source.doc_title }} · {{ source.clause_no }}
              </router-link>
            </div>
          </el-collapse-item>
        </el-collapse>
      </article>
    </div>

    <div v-if="pages > 1" class="pager">
      <el-pagination
        background
        layout="prev, pager, next"
        :page-size="size"
        :current-page="page"
        :total="total"
        @current-change="go"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import { api } from '@/api'
import type { HistoryItem } from '@/api/types'
import { useSessionStore } from '@/stores/session'
import { errorCode as toCode, errorMessage } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

import ConfidenceBadge from '@/components/ConfidenceBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import MarkdownView from '@/components/MarkdownView.vue'

const router = useRouter()
const session = useSessionStore()

const items = ref<HistoryItem[]>([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<number | null>(null)

const pages = computed(() => Math.max(1, Math.ceil(total.value / size)))

async function load(targetPage: number): Promise<void> {
  loading.value = true
  error.value = null
  errorCode.value = null
  try {
    const result = await api.getHistory({
      session_id: session.sessionId,
      page: targetPage,
      size,
    })
    items.value = result.items
    total.value = result.total
    page.value = result.page || targetPage
  } catch (e: unknown) {
    items.value = []
    total.value = 0
    error.value = errorMessage(e, '历史加载失败')
    errorCode.value = toCode(e)
  } finally {
    loading.value = false
  }
}

function go(targetPage: number): void {
  if (targetPage < 1 || targetPage > pages.value) return
  void load(targetPage)
}

async function removeOne(item: HistoryItem): Promise<void> {
  try {
    await ElMessageBox.confirm('确定删除这条问答记录？', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.deleteHistory({ qa_id: item.qa_id })
    ElMessage.success('已删除')
    const nextPage = items.value.length === 1 && page.value > 1 ? page.value - 1 : page.value
    await load(nextPage)
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e, '删除失败'))
  }
}

async function clearAll(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定清空当前会话的全部历史？此操作不可恢复。', '清空确认', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const result = await api.deleteHistory({ session_id: session.sessionId })
    ElMessage.success(`已清空 ${result.deleted} 条`)
    items.value = []
    total.value = 0
    page.value = 1
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e, '清空失败'))
  }
}

onMounted(() => {
  void load(1)
})
</script>

<style scoped>
.history-page {
  max-width: 900px;
  margin: 0 auto;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
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
  word-break: break-all;
}
.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-item {
  padding: 12px 14px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
}
.history-item__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.history-item__question {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--rag-text);
  word-break: break-word;
}
.history-item__meta {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.history-item__collapse {
  margin-top: 8px;
}
.history-item__collapse :deep(.el-collapse-item__header) {
  font-size: 12px;
  color: var(--rag-text-2);
  height: 32px;
}
.history-item__sources {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--rag-border);
  font-size: 12px;
}
.history-item__source {
  color: var(--rag-primary);
  text-decoration: none;
}
.history-item__source:hover {
  text-decoration: underline;
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}
@media (max-width: 768px) {
  .history-item__head {
    flex-direction: column;
  }
}
</style>
