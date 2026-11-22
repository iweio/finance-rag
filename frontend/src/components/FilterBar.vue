<template>
  <div class="filter-bar" :class="{ 'filter-bar--compact': compact }">
    <div class="filter-bar__row">
      <el-select
        class="filter-bar__select"
        :model-value="modelValue.domain ?? ''"
        placeholder="业务领域"
        clearable
        :disabled="loading"
        @update:model-value="onDomain"
      >
        <el-option
          v-for="option in dict?.domain ?? []"
          :key="option.value"
          :label="`${option.label}（${option.count}）`"
          :value="option.value"
        />
      </el-select>

      <el-select
        class="filter-bar__select"
        :model-value="modelValue.status ?? ''"
        placeholder="效力状态"
        clearable
        :disabled="loading"
        @update:model-value="onStatus"
      >
        <el-option
          v-for="option in dict?.status ?? []"
          :key="option.value"
          :label="`${option.label}（${option.count}）`"
          :value="option.value"
        />
      </el-select>

      <el-select
        class="filter-bar__select filter-bar__select--wide"
        :model-value="modelValue.org ?? ''"
        placeholder="发文机关"
        clearable
        filterable
        :disabled="loading"
        @update:model-value="onOrg"
      >
        <el-option
          v-for="option in dict?.org ?? []"
          :key="option.value"
          :label="`${option.label}（${option.count}）`"
          :value="option.value"
        />
      </el-select>

      <el-button v-if="activeCount > 0" text type="primary" @click="reset">清除筛选</el-button>
    </div>

    <div v-if="!compact" class="filter-bar__dict">
      <div class="filter-bar__dict-row">
        <span class="filter-bar__dict-label">文件类型</span>
        <el-tag
          v-for="option in dict?.doc_type ?? []"
          :key="option.value"
          size="small"
          effect="plain"
          round
          class="filter-bar__chip"
        >
          {{ option.label }} {{ option.count }}
        </el-tag>
      </div>

      <div class="filter-bar__dict-row">
        <span class="filter-bar__dict-label">主题标签</span>
        <el-tag
          v-for="option in dict?.topic_tags ?? []"
          :key="option.value"
          size="small"
          effect="plain"
          round
          class="filter-bar__chip"
        >
          {{ option.label }} {{ option.count }}
        </el-tag>
      </div>

      <p class="filter-bar__hint">
        以上两行仅作字典展示；契约的 <code>filters</code> 只有领域 / 效力 / 机关三个字段（主题标签按裁决 #1 不入检索）。
      </p>
      <p v-if="dict" class="filter-bar__range">
        库内施行日期范围：{{ formatDateRange(dict.date_range) }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { FilterDict, Filters } from '@/api/types'
import { formatDateRange } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    modelValue: Filters
    dict: FilterDict | null
    loading?: boolean
    compact?: boolean
  }>(),
  { loading: false, compact: false },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: Filters): void
}>()

const activeCount = computed(
  () =>
    [props.modelValue.domain, props.modelValue.status, props.modelValue.org].filter(
      (value) => typeof value === 'string' && value.length > 0,
    ).length,
)

function update(key: 'domain' | 'status' | 'org', value: string): void {
  emit('update:modelValue', {
    ...props.modelValue,
    [key]: value ? value : null,
  })
}

function onDomain(value: string): void {
  update('domain', value)
}
function onStatus(value: string): void {
  update('status', value)
}
function onOrg(value: string): void {
  update('org', value)
}
function reset(): void {
  emit('update:modelValue', { domain: null, status: null, org: null })
}
</script>

<style scoped>
.filter-bar {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.filter-bar__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-bar__select {
  width: 150px;
}
.filter-bar__select--wide {
  width: 220px;
}
.filter-bar__dict {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 2px;
}
.filter-bar__dict-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.filter-bar__dict-label {
  flex: none;
  width: 64px;
  font-size: 12px;
  color: var(--rag-text-3);
}
.filter-bar__chip {
  font-variant-numeric: tabular-nums;
}
.filter-bar__hint {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--rag-text-3);
}
.filter-bar__hint code {
  padding: 0 3px;
  border-radius: 3px;
  background: var(--rag-fill);
}
.filter-bar__range {
  margin: 0;
  font-size: 12px;
  color: var(--rag-text-3);
  font-variant-numeric: tabular-nums;
}
.filter-bar--compact .filter-bar__select {
  width: 132px;
}
.filter-bar--compact .filter-bar__select--wide {
  width: 180px;
}
@media (max-width: 768px) {
  .filter-bar__select,
  .filter-bar__select--wide {
    width: 100%;
  }
}
</style>
