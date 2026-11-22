<template>
  <div class="user-menu">
    <el-dropdown v-if="auth.isLoggedIn" trigger="click" @command="onCommand">
      <button type="button" class="user-menu__trigger">
        <el-avatar :size="26" class="user-menu__avatar">{{ initial }}</el-avatar>
        <span class="user-menu__name">{{ auth.displayName || '已登录' }}</span>
        <el-icon :size="12"><ArrowDown /></el-icon>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="history">我的历史</el-dropdown-item>
          <el-dropdown-item command="favorites">我的收藏</el-dropdown-item>
          <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <el-button v-else text type="primary" @click="toLogin">登录 / 注册</el-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

import { useAuthStore } from '@/stores/auth'
import { useFavoriteStore } from '@/stores/favorite'

const auth = useAuthStore()
const favorite = useFavoriteStore()
const router = useRouter()
const route = useRoute()

const initial = computed(() => (auth.displayName ? auth.displayName.slice(0, 1).toUpperCase() : '?'))

function toLogin(): void {
  router.push({ path: '/login', query: { redirect: route.fullPath } })
}

function onCommand(command: string): void {
  if (command === 'history') {
    router.push('/history')
    return
  }
  if (command === 'favorites') {
    router.push('/favorites')
    return
  }
  if (command === 'logout') {
    auth.logout()
    favorite.reset()
    ElMessage.success('已退出登录')
    if (route.meta.requiresAuth) router.replace('/chat')
  }
}
</script>

<style scoped>
.user-menu {
  display: flex;
  align-items: center;
}
.user-menu__trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px 2px 2px;
  border: 1px solid var(--rag-border);
  border-radius: 999px;
  background: var(--rag-surface);
  cursor: pointer;
  color: var(--rag-text);
}
.user-menu__trigger:hover {
  border-color: var(--rag-primary-200);
}
.user-menu__avatar {
  background: var(--rag-primary);
  color: #fff;
  font-size: 12px;
}
.user-menu__name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
</style>
