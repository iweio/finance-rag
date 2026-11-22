<template>
  <el-descriptions class="meta-table" :column="2" border size="default">
    <el-descriptions-item label="所属法规" :span="2">
      {{ detail.doc_title || EMPTY }}
    </el-descriptions-item>
    <el-descriptions-item label="条文编号">{{ detail.clause_no || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="条文序号">{{ clauseIndexText }}</el-descriptions-item>
    <el-descriptions-item label="文号">{{ detail.doc_number || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="发文机关">{{ detail.org || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="文件类型">{{ detail.doc_type || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="业务领域">{{ detail.domain || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="效力状态">
      <el-tag size="small" :type="statusType">{{ detail.status || '—' }}</el-tag>
    </el-descriptions-item>
    <el-descriptions-item label="公布日期">{{ formatDate(detail.publish_date) }}</el-descriptions-item>
    <el-descriptions-item label="施行日期">{{ formatDate(detail.effective_date) }}</el-descriptions-item>
    <el-descriptions-item label="失效日期">{{ formatDate(detail.expire_date) }}</el-descriptions-item>
    <el-descriptions-item label="采集日期">{{ formatDate(detail.fetched_at) }}</el-descriptions-item>
    <el-descriptions-item label="所属章节" :span="2">{{ detail.chapter || EMPTY }}</el-descriptions-item>
    <el-descriptions-item label="来源链接" :span="2">
      <a
        v-if="detail.source_url"
        :href="detail.source_url"
        target="_blank"
        rel="noopener noreferrer"
        class="meta-table__link"
      >
        {{ detail.source_url }}
      </a>
      <span v-else class="meta-table__muted">来源页未收录</span>
    </el-descriptions-item>
    <el-descriptions-item label="条文 ID">{{ detail.clause_id }}</el-descriptions-item>
    <el-descriptions-item label="文档 ID">{{ detail.doc_id || EMPTY }}</el-descriptions-item>
  </el-descriptions>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { ClauseDetail } from '@/api/types'
import { EMPTY, formatDate } from '@/utils/format'

const props = defineProps<{ detail: ClauseDetail }>()

const clauseIndexText = computed(() =>
  props.detail.clause_index > 0 ? String(props.detail.clause_index) : EMPTY,
)

const statusType = computed<'success' | 'warning' | 'info' | 'danger'>(() => {
  if (props.detail.status === '有效') return 'success'
  if (props.detail.status === '尚未生效') return 'warning'
  if (props.detail.status === '已废止') return 'info'
  return 'info'
})
</script>

<style scoped>
.meta-table {
  margin: 12px 0;
}
.meta-table__link {
  color: var(--rag-primary);
  text-decoration: none;
  word-break: break-all;
}
.meta-table__link:hover {
  text-decoration: underline;
}
.meta-table__muted {
  color: var(--rag-text-3);
}
</style>
