<template>
  <div class="app-shell">
    <aside class="app-sidebar" :class="{ 'is-open': drawerOpen }">
      <div class="app-sidebar__brand">
        <div class="app-sidebar__logo">RAG</div>
        <div class="app-sidebar__brand-text">
          <strong>金融制度知识库</strong>
          <span>条文级检索问答</span>
        </div>
      </div>

      <nav class="app-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="app-nav__item"
          :class="{ 'is-active': isActive(item.path) }"
          @click="closeDrawer"
        >
          <el-icon :size="17"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <el-icon v-if="item.auth && !auth.isLoggedIn" class="app-nav__lock" :size="12">
            <Lock />
          </el-icon>
        </router-link>
      </nav>

      <div class="app-sidebar__foot">
        <span class="app-sidebar__version">v{{ health?.version ?? '0.1.0' }}</span>
        <el-tag v-if="IS_MOCK" size="small" type="warning" effect="plain">Mock</el-tag>
      </div>
    </aside>

    <div v-if="drawerOpen" class="app-mask" @click="closeDrawer" />

    <div class="app-main">
      <header class="app-topbar">
        <button
          type="button"
          class="app-topbar__burger"
          aria-label="打开菜单"
          @click="drawerOpen = true"
        >
          <el-icon :size="20"><Expand /></el-icon>
        </button>

        <div class="app-topbar__title">{{ pageTitle }}</div>

        <div class="app-topbar__right">
          <button
            type="button"
            class="health-chip"
            :class="{ 'is-ok': isHealthy, 'is-down': isDown }"
            :title="healthTitle"
            @click="loadHealth"
          >
            <span class="health-chip__dot" />
            <span v-if="healthLoading">检查中…</span>
            <span v-else-if="healthError || isDown">依赖不可用</span>
            <span v-else-if="health">知识库 {{ rowCountLabel }} 条</span>
            <span v-else>健康检查</span>
          </button>
          <UserMenu />
        </div>
      </header>

      <main class="app-content">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Clock, Expand, Lock, Search, Share, Star } from '@element-plus/icons-vue'

import { api, IS_MOCK } from '@/api'
import type { Health } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/utils/error'
import { formatCount } from '@/utils/format'

import UserMenu from './UserMenu.vue'

const route = useRoute()
const auth = useAuthStore()

const drawerOpen = ref(false)
const health = ref<Health | null>(null)
const healthLoading = ref(false)
const healthError = ref<string | null>(null)

const navItems = [
  { path: '/chat', label: '问答', icon: ChatDotRound, auth: false },
  { path: '/search', label: '检索', icon: Search, auth: false },
  { path: '/graph', label: '图谱', icon: Share, auth: false },
  { path: '/history', label: '历史', icon: Clock, auth: true },
  { path: '/favorites', label: '收藏', icon: Star, auth: true },
] as const

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? '金融制度知识库')

const rowCountLabel = computed(() => formatCount(health.value?.milvus.row_count))

const isDown = computed(() => {
  const current = health.value
  if (!current) return false
  const mysqlOk = current.mysql.status === 'ok'
  const milvusOk = current.milvus.status === 'ok' || current.milvus.status === 'mock'
  return !mysqlOk || !milvusOk
})

const isHealthy = computed(() => !!health.value && !isDown.value && !healthError.value)

const healthTitle = computed(() => {
  if (healthError.value) return `${healthError.value}（点击重试）`
  const current = health.value
  if (!current) return '点击检查后端 / Milvus / MySQL'
  return [
    `服务 ${current.service}`,
    `MySQL ${current.mysql.status}`,
    `Milvus ${current.milvus.status}（${current.milvus.collection}）`,
    current.mode ? `模式 ${current.mode}` : '',
  ]
    .filter(Boolean)
    .join(' · ')
})

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

function closeDrawer(): void {
  drawerOpen.value = false
}

async function loadHealth(): Promise<void> {
  if (healthLoading.value) return
  healthLoading.value = true
  healthError.value = null
  try {
    health.value = await api.getHealth()
  } catch (error: unknown) {
    health.value = null
    healthError.value = errorMessage(error, '健康检查失败')
  } finally {
    healthLoading.value = false
  }
}

onMounted(loadHealth)
watch(
  () => route.fullPath,
  () => closeDrawer(),
)
</script>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  background: var(--rag-bg);
}
.app-sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: var(--rag-sidebar-w);
  background: var(--rag-surface);
  border-right: 1px solid var(--rag-border);
}
.app-sidebar__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--rag-topbar-h);
  padding: 0 16px;
  border-bottom: 1px solid var(--rag-border);
}
.app-sidebar__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--rag-primary), var(--rag-primary-600));
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.app-sidebar__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
  min-width: 0;
}
.app-sidebar__brand-text strong {
  font-size: 13px;
  color: var(--rag-text);
  white-space: nowrap;
}
.app-sidebar__brand-text span {
  font-size: 11px;
  color: var(--rag-text-3);
}
.app-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 10px;
  flex: 1;
}
.app-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--rag-radius-sm);
  color: var(--rag-text-2);
  font-size: 14px;
  text-decoration: none;
  transition: background 0.15s ease, color 0.15s ease;
}
.app-nav__item:hover {
  background: var(--rag-fill);
  color: var(--rag-text);
}
.app-nav__item.is-active {
  background: var(--rag-primary-50);
  color: var(--rag-primary);
  font-weight: 600;
}
.app-nav__lock {
  margin-left: auto;
  color: var(--rag-text-3);
}
.app-sidebar__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--rag-border);
  font-size: 12px;
  color: var(--rag-text-3);
}
.app-main {
  flex: 1;
  min-width: 0;
  margin-left: var(--rag-sidebar-w);
  display: flex;
  flex-direction: column;
}
.app-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--rag-topbar-h);
  padding: 0 20px;
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--rag-border);
}
.app-topbar__burger {
  display: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--rag-radius-sm);
  background: transparent;
  color: var(--rag-text-2);
  cursor: pointer;
}
.app-topbar__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--rag-text);
}
.app-topbar__right {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}
.health-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rag-border);
  border-radius: 999px;
  background: var(--rag-surface);
  color: var(--rag-text-2);
  font-size: 12px;
  cursor: pointer;
}
.health-chip__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rag-text-3);
}
.health-chip.is-ok .health-chip__dot {
  background: var(--rag-success);
}
.health-chip.is-down .health-chip__dot {
  background: var(--rag-danger);
}
.health-chip.is-down {
  color: var(--rag-danger);
  border-color: var(--rag-danger-200);
}
.health-chip:hover {
  border-color: var(--rag-primary-200);
}
.app-content {
  flex: 1;
  min-width: 0;
  padding: 20px;
}
.app-mask {
  position: fixed;
  inset: 0;
  z-index: 25;
  background: rgba(16, 24, 40, 0.35);
}
@media (max-width: 900px) {
  .app-sidebar {
    transform: translateX(-100%);
    transition: transform 0.2s ease;
    box-shadow: var(--rag-shadow);
  }
  .app-sidebar.is-open {
    transform: translateX(0);
  }
  .app-main {
    margin-left: 0;
  }
  .app-topbar__burger {
    display: inline-flex;
  }
  .app-content {
    padding: 12px;
  }
}
@media (min-width: 901px) {
  .app-mask {
    display: none;
  }
}
</style>
