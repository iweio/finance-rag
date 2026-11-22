<template>
  <el-alert type="error" :closable="false" show-icon class="error-banner">
    <template #title>
      <span class="error-banner__title">{{ title }}</span>
      <el-tag v-if="code" size="small" type="danger" effect="plain" class="error-banner__code">
        {{ code }}
      </el-tag>
    </template>
    <div class="error-banner__body">
      <p class="error-banner__message">{{ message }}</p>
      <div v-if="$slots.action" class="error-banner__action">
        <slot name="action" />
      </div>
    </div>
  </el-alert>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    message: string
    code?: number | null
    title?: string
  }>(),
  { code: null, title: '出错了' },
)
</script>

<style scoped>
.error-banner {
  margin: 12px 0;
}
.error-banner__title {
  font-weight: 600;
}
.error-banner__code {
  margin-left: 8px;
  font-variant-numeric: tabular-nums;
}
.error-banner__message {
  margin: 4px 0 0;
  line-height: 1.7;
  word-break: break-word;
}
.error-banner__action {
  margin-top: 8px;
}
</style>
