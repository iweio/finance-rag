<template>
  <div class="favorite-page">
    <header class="page-head">
      <div>
        <h1>我的收藏</h1>
        <p>共 <strong>{{ favorite.total }}</strong> 条收藏（需登录）</p>
      </div>
      <el-button text @click="reload">刷新</el-button>
    </header>

    <LoadingSkeleton v-if="favorite.loading" :rows="5" variant="card" />

    <ErrorBanner
      v-else-if="favorite.error"
      :message="favorite.error"
      code=""
      title="收藏加载失败"
    >
      <template #action>
        <el-button size="small" @click="reload">重试</el-button>
      </template>
    </ErrorBanner>

    <EmptyState
      v-else-if="favorite.items.length === 0"
      title="还没有收藏"
      description="在条文详情页点「收藏」，这里就会出现。"
    >
      <template #action>
        <el-button type="primary" @click="router.push('/search')">去检索条文</el-button>
      </template>
    </EmptyState>

    <ul v-else class="favorite-list">
      <li v-for="item in favorite.items" :key="item.favorite_id" class="favorite-item">
        <div class="favorite-item__main">
          <router-link
            class="favorite-item__title"
            :to="`/clause/${encodeURIComponent(item.clause_id)}`"
          >
            {{ item.doc_title }}
          </router-link>
          <el-tag size="small" type="info" effect="plain">{{ item.clause_no }}</el-tag>
          <span class="favorite-item__date muted">
            收藏于 {{ formatDateTime(item.created_at) }}
          </span>
        </div>
        <div class="favorite-item__actions">
          <router-link
            class="favorite-item__link"
            :to="`/clause/${encodeURIComponent(item.clause_id)}`"
          >
            查看条文 →
          </router-link>
          <el-button text type="danger" size="small" @click="remove(item)">取消收藏</el-button>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import type { FavoriteItem } from '@/api/types'
import { useFavoriteStore } from '@/stores/favorite'
import { errorMessage } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

import EmptyState from '@/components/EmptyState.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const router = useRouter()
const favorite = useFavoriteStore()

function reload(): void {
  void favorite.load(true)
}

async function remove(item: FavoriteItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`取消收藏「${item.doc_title} ${item.clause_no}」？`, '取消收藏', {
      type: 'warning',
      confirmButtonText: '取消收藏',
      cancelButtonText: '保留',
    })
  } catch {
    return
  }
  try {
    await favorite.remove(item.favorite_id)
    ElMessage.success('已取消收藏')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e, '取消收藏失败'))
  }
}

onMounted(() => {
  void favorite.load(true)
})
</script>

<style scoped>
.favorite-page {
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
}
.page-head strong {
  color: var(--rag-text);
  font-variant-numeric: tabular-nums;
}
.favorite-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.favorite-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
}
.favorite-item__main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}
.favorite-item__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--rag-text);
  text-decoration: none;
}
.favorite-item__title:hover {
  color: var(--rag-primary);
}
.favorite-item__date {
  font-size: 12px;
}
.favorite-item__actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.favorite-item__link {
  color: var(--rag-primary);
  text-decoration: none;
}
.favorite-item__link:hover {
  text-decoration: underline;
}
@media (max-width: 768px) {
  .favorite-item {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
