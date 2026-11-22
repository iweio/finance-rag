<template>
  <div class="empty-state">
    <el-icon class="empty-state__icon" :size="38">
      <component :is="iconComponent" />
    </el-icon>
    <h3 class="empty-state__title">{{ title }}</h3>
    <p v-if="description" class="empty-state__desc">{{ description }}</p>
    <div v-if="$slots.action" class="empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Document, Search, Warning } from '@element-plus/icons-vue'

type IconName = 'document' | 'search' | 'warning'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    icon?: IconName
  }>(),
  { description: '', icon: 'document' },
)

const iconComponent = computed(() => {
  if (props.icon === 'search') return Search
  if (props.icon === 'warning') return Warning
  return Document
})
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 20px;
  text-align: center;
  color: var(--rag-text-2);
}
.empty-state__icon {
  color: var(--rag-text-3);
}
.empty-state__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--rag-text);
}
.empty-state__desc {
  margin: 0;
  max-width: 460px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--rag-text-3);
}
.empty-state__action {
  margin-top: 8px;
}
</style>
