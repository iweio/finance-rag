<template>
  <div class="clause-page">
    <div class="clause-actions">
      <el-button text @click="goBack">← 返回</el-button>
      <div class="clause-actions__right">
        <el-button
          :type="isFavorite ? 'warning' : 'primary'"
          :plain="isFavorite"
          :loading="favoriteLoading"
          @click="toggleFavorite"
        >
          <el-icon>
            <StarFilled v-if="isFavorite" />
            <Star v-else />
          </el-icon>
          {{ isFavorite ? '已收藏' : '收藏' }}
        </el-button>
      </div>
    </div>

    <LoadingSkeleton v-if="loading" :rows="8" variant="card" />

    <ErrorBanner v-else-if="error" :message="error" :code="errorCode" title="条文加载失败">
      <template #action>
        <el-button size="small" @click="load">重试</el-button>
        <el-button size="small" text @click="goSearch">去检索</el-button>
      </template>
    </ErrorBanner>

    <template v-else-if="detail">
      <header class="clause-head">
        <h1 class="clause-head__title">{{ detail.doc_title }}</h1>
        <div class="clause-head__meta">
          <el-tag>{{ detail.clause_no }}</el-tag>
          <el-tag type="info" effect="plain">{{ detail.doc_type || '文件类型未知' }}</el-tag>
          <el-tag :type="statusType" effect="light">{{ detail.status || '—' }}</el-tag>
          <span class="muted">施行 {{ formatDate(detail.effective_date) }}</span>
        </div>
      </header>

      <section class="panel">
        <h2 class="panel__title">条文全文</h2>
        <p class="clause-text">{{ detail.text }}</p>
      </section>

      <section class="panel">
        <h2 class="panel__title">元数据</h2>
        <ClauseMetaTable :detail="detail" />
      </section>

      <section class="panel">
        <h2 class="panel__title">
          关联条文
          <span v-if="related.length" class="panel__count">{{ related.length }}</span>
        </h2>
        <RelatedGroup :items="related" :loading="relatedLoading" />
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Star, StarFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

import { api } from '@/api'
import type { ClauseDetail, RelatedClause } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useFavoriteStore } from '@/stores/favorite'
import { errorCode as toCode, errorMessage } from '@/utils/error'
import { formatDate } from '@/utils/format'

import ClauseMetaTable from '@/components/ClauseMetaTable.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import RelatedGroup from '@/components/RelatedGroup.vue'

const props = defineProps<{ clauseId: string }>()

const router = useRouter()
const auth = useAuthStore()
const favorite = useFavoriteStore()

const detail = ref<ClauseDetail | null>(null)
const related = ref<RelatedClause[]>([])
const loading = ref(false)
const relatedLoading = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<number | null>(null)
const favoriteLoading = ref(false)

const isFavorite = computed(() => favorite.isFavorite(props.clauseId))

const statusType = computed<'success' | 'warning' | 'info'>(() => {
  if (detail.value?.status === '有效') return 'success'
  if (detail.value?.status === '尚未生效') return 'warning'
  return 'info'
})

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  errorCode.value = null
  detail.value = null
  related.value = []

  try {
    detail.value = await api.getClause(props.clauseId)
  } catch (e: unknown) {
    error.value = errorMessage(e, '条文加载失败')
    errorCode.value = toCode(e)
    loading.value = false
    return
  }
  loading.value = false

  relatedLoading.value = true
  try {
    const result = await api.getRelated(props.clauseId, 8)
    related.value = result.related
  } catch {
    // 关联查询失败不影响正文展示
    related.value = []
  } finally {
    relatedLoading.value = false
  }
}

function goBack(): void {
  if (window.history.length > 1) router.back()
  else void router.push('/search')
}

function goSearch(): void {
  void router.push('/search')
}

async function toggleFavorite(): Promise<void> {
  if (!auth.isLoggedIn) {
    ElMessage.warning('登录后可收藏条文')
    void router.push({ path: '/login', query: { redirect: `/clause/${props.clauseId}` } })
    return
  }
  if (favoriteLoading.value) return
  favoriteLoading.value = true
  try {
    const existing = favorite.items.find((item) => item.clause_id === props.clauseId)
    if (existing) {
      await favorite.remove(existing.favorite_id)
      ElMessage.success('已取消收藏')
    } else {
      await favorite.add(props.clauseId)
      ElMessage.success('已收藏')
    }
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e, '收藏操作失败'))
  } finally {
    favoriteLoading.value = false
  }
}

watch(() => props.clauseId, load, { immediate: true })

onMounted(() => {
  if (auth.isLoggedIn) void favorite.load()
})
</script>

<style scoped>
.clause-page {
  max-width: 960px;
  margin: 0 auto;
}
.clause-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.clause-head {
  margin-bottom: 14px;
}
.clause-head__title {
  margin: 0 0 8px;
  font-size: 20px;
  line-height: 1.4;
  color: var(--rag-text);
}
.clause-head__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
}
.panel {
  margin-bottom: 14px;
  padding: 16px 18px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
}
.panel__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--rag-text);
}
.panel__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--rag-fill);
  color: var(--rag-text-3);
  font-size: 11px;
  font-weight: 400;
}
.clause-text {
  margin: 0;
  font-size: 14px;
  line-height: 2;
  color: var(--rag-text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
