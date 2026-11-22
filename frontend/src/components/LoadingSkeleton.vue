<template>
  <div class="loading-skeleton" aria-busy="true" aria-live="polite">
    <el-skeleton :loading="true" animated>
      <template #template>
        <el-skeleton-item
          v-if="variant === 'card'"
          variant="rect"
          class="loading-skeleton__card"
        />
        <div v-for="row in rows" :key="row" class="loading-skeleton__row">
          <el-skeleton-item
            variant="text"
            :style="{ width: row % 3 === 0 ? '62%' : row % 2 === 0 ? '88%' : '100%' }"
          />
        </div>
      </template>
    </el-skeleton>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    rows?: number
    variant?: 'lines' | 'card'
  }>(),
  { rows: 4, variant: 'lines' },
)
</script>

<style scoped>
.loading-skeleton {
  width: 100%;
}
.loading-skeleton__card {
  width: 100%;
  height: 104px;
  border-radius: var(--rag-radius);
  margin-bottom: 6px;
}
.loading-skeleton__row {
  margin-top: 10px;
}
</style>
