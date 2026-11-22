<template>
  <LoadingSkeleton v-if="loading" :rows="4" />

  <EmptyState
    v-else-if="groups.length === 0"
    title="暂无关联条文"
    description="该条文暂时没有同条 / 同章 / 引用 / 相似关系。"
    icon="search"
  />

  <div v-else class="related-groups">
    <section v-for="group in groups" :key="group.type" class="related-group">
      <header class="related-group__head">
        <el-tag :type="group.tagType" size="small" effect="light">{{ group.type }}</el-tag>
        <span class="related-group__count">{{ group.items.length }} 条</span>
      </header>

      <ul class="related-group__list">
        <li
          v-for="(item, index) in group.items"
          :key="`${item.clause_id}-${index}`"
          class="related-group__item"
        >
          <router-link
            class="related-group__link"
            :to="`/clause/${encodeURIComponent(item.clause_id)}`"
          >
            {{ item.doc_title }} · {{ item.clause_no }}
          </router-link>
          <span class="related-group__score">{{ formatScore(item.score) }}</span>
          <p class="related-group__snippet">{{ item.snippet }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { RelatedClause } from '@/api/types'
import { formatScore } from '@/utils/format'

import EmptyState from './EmptyState.vue'
import LoadingSkeleton from './LoadingSkeleton.vue'

type TagType = 'primary' | 'success' | 'warning' | 'info'

interface RelationGroup {
  type: string
  tagType: TagType
  items: RelatedClause[]
}

const props = withDefaults(
  defineProps<{
    items: RelatedClause[]
    loading?: boolean
  }>(),
  { loading: false },
)

const ORDER = ['同条', '同章', '引用', '相似'] as const
const TAG_TYPES: Record<string, TagType> = {
  同条: 'primary',
  同章: 'success',
  引用: 'warning',
  相似: 'info',
}

const groups = computed<RelationGroup[]>(() =>
  ORDER.map((type) => ({
    type,
    tagType: TAG_TYPES[type] ?? 'info',
    items: props.items
      .filter((item) => item.relation_type === type)
      .sort((a, b) => b.score - a.score),
  })).filter((group) => group.items.length > 0),
)
</script>

<style scoped>
.related-groups {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.related-group__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.related-group__count {
  font-size: 12px;
  color: var(--rag-text-3);
}
.related-group__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.related-group__item {
  padding: 10px 12px;
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius-sm);
  background: var(--rag-surface);
}
.related-group__link {
  font-weight: 600;
  font-size: 13px;
  color: var(--rag-text);
  text-decoration: none;
}
.related-group__link:hover {
  color: var(--rag-primary);
}
.related-group__score {
  float: right;
  font-size: 12px;
  color: var(--rag-text-3);
  font-variant-numeric: tabular-nums;
}
.related-group__snippet {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--rag-text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
