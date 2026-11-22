<template>
  <AppShell>
    <router-view v-slot="{ Component }">
      <component :is="Component" />
    </router-view>
  </AppShell>
</template>

<script setup lang="ts">
/**
 * 根组件只做两件事：套上 AppShell（侧栏/顶栏/健康指示），把当前路由渲染进 slot。
 * 页面自己的布局一律在 view 里做，别往这里堆条件分支。
 */
import AppShell from '@/components/AppShell.vue'
</script>
