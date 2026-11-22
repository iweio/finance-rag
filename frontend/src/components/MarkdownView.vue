<template>
  <div class="markdown-body" :class="{ 'is-streaming': streaming }" v-html="html" />
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { renderMarkdown } from '@/utils/markdown'

const props = withDefaults(
  defineProps<{
    content: string
    streaming?: boolean
  }>(),
  { streaming: false },
)

// markdown-it 已关闭 html，输出再过 DOMPurify；仍用 v-html 是因为要渲染答案排版
const html = computed(() => renderMarkdown(props.content))
</script>

<style scoped>
.markdown-body.is-streaming :deep(> *:last-child)::after {
  content: '▍';
  margin-left: 2px;
  color: var(--rag-primary);
  animation: rag-blink 1s steps(2, start) infinite;
}
@keyframes rag-blink {
  to {
    visibility: hidden;
  }
}
</style>
