<template>
  <span class="confidence">
    <el-tag :type="tagType" size="small" effect="light" round>
      可信度 {{ levelText }}<template v-if="showValue && hasScore"> · {{ formatted }}</template>
    </el-tag>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { EMPTY, formatScore } from '@/utils/format'

type Level = 'high' | 'mid' | 'low' | 'none'

const props = withDefaults(
  defineProps<{
    score: number | null | undefined
    showValue?: boolean
  }>(),
  { showValue: true },
)

const hasScore = computed(() => typeof props.score === 'number' && !Number.isNaN(props.score))

const level = computed<Level>(() => {
  if (!hasScore.value) return 'none'
  const score = props.score as number
  if (score >= 0.7) return 'high'
  if (score >= 0.4) return 'mid'
  return 'low'
})

const levelText = computed(() => {
  const map: Record<Level, string> = { high: '高', mid: '中', low: '低', none: EMPTY }
  return map[level.value]
})

const tagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  const map: Record<Level, 'success' | 'warning' | 'danger' | 'info'> = {
    high: 'success',
    mid: 'warning',
    low: 'danger',
    none: 'info',
  }
  return map[level.value]
})

const formatted = computed(() => formatScore(props.score))
</script>

<style scoped>
.confidence {
  display: inline-flex;
  align-items: center;
}
</style>
