<template>
  <article class="source-card" :class="{ 'source-card--expanded': expanded }">
    <header class="source-card__head">
      <span v-if="rank" class="source-card__rank">{{ rank }}</span>
      <div class="source-card__title">
        <router-link class="source-card__doc" :to="detailLink">{{ source.doc_title }}</router-link>
        <el-tag size="small" type="info" effect="plain">{{ source.clause_no }}</el-tag>
      </div>
      <ConfidenceBadge :score="source.score" :show-value="false" />
    </header>

    <p v-if="source.chapter" class="source-card__chapter">{{ source.chapter }}</p>

    <p class="source-card__snippet" :class="{ 'is-clamped': isLong && !expanded }">
      {{ source.snippet }}
    </p>
    <button v-if="isLong" type="button" class="source-card__toggle" @click="expanded = !expanded">
      {{ expanded ? '收起' : '展开全文' }}
    </button>

    <footer class="source-card__foot">
      <el-tag size="small" :type="statusType">{{ source.status || '—' }}</el-tag>
      <span class="source-card__date">施行 {{ formatDate(source.effective_date) }}</span>
      <a
        v-if="source.source_url"
        :href="source.source_url"
        target="_blank"
        rel="noopener noreferrer"
        class="source-card__link"
      >
        查看原文
      </a>
      <span v-else class="source-card__muted">来源页未收录</span>
      <router-link class="source-card__detail" :to="detailLink">条文详情 →</router-link>
    </footer>
  </article>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ClauseBrief } from '@/api/types'
import { formatDate } from '@/utils/format'

import ConfidenceBadge from './ConfidenceBadge.vue'

const props = withDefaults(
  defineProps<{
    source: ClauseBrief
    rank?: number
  }>(),
  { rank: 0 },
)

const expanded = ref(false)
const isLong = computed(() => props.source.snippet.length > 120)
const detailLink = computed(() => `/clause/${encodeURIComponent(props.source.clause_id)}`)

const statusType = computed<'success' | 'warning' | 'info' | 'danger'>(() => {
  if (props.source.status === '有效') return 'success'
  if (props.source.status === '尚未生效') return 'warning'
  if (props.source.status === '已废止') return 'info'
  return 'info'
})
</script>

<style scoped>
.source-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.source-card:hover {
  border-color: var(--rag-primary-200);
  box-shadow: var(--rag-shadow-sm);
}
.source-card__head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.source-card__rank {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-top: 2px;
  border-radius: 50%;
  background: var(--rag-primary-50);
  color: var(--rag-primary);
  font-size: 12px;
  font-weight: 600;
}
.source-card__title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.source-card__doc {
  font-weight: 600;
  color: var(--rag-text);
  text-decoration: none;
}
.source-card__doc:hover {
  color: var(--rag-primary);
}
.source-card__chapter {
  margin: 0;
  font-size: 12px;
  color: var(--rag-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-card__snippet {
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--rag-text-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.source-card__snippet.is-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.source-card__toggle {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: none;
  color: var(--rag-primary);
  font-size: 12px;
  cursor: pointer;
}
.source-card__toggle:hover {
  text-decoration: underline;
}
.source-card__foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 4px;
  font-size: 12px;
  color: var(--rag-text-3);
}
.source-card__date {
  font-variant-numeric: tabular-nums;
}
.source-card__link,
.source-card__detail {
  color: var(--rag-primary);
  text-decoration: none;
}
.source-card__link:hover,
.source-card__detail:hover {
  text-decoration: underline;
}
.source-card__detail {
  margin-left: auto;
}
.source-card__muted {
  color: var(--rag-text-3);
}
</style>
