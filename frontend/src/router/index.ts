/**
 * 路由表（计划 §4.3）。
 *
 * 三条规矩：
 * 1. `/history`、`/favorites` 挂登录守卫（`meta.requiresAuth`），未登录跳 `/login?redirect=`；
 * 2. `meta.title` 给 `AppShell` 的顶栏用，不写死中文标题在组件里；
 * 3. 404 兜底必须放在最后，否则它会先吃掉所有路径。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { pinia } from '@/stores'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/chat' },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '问答' },
  },
  {
    path: '/search',
    name: 'search',
    component: () => import('@/views/SearchView.vue'),
    meta: { title: '条文检索' },
  },
  {
    path: '/clause/:clauseId',
    name: 'clause',
    component: () => import('@/views/ClauseView.vue'),
    meta: { title: '条文详情' },
    props: true,
  },
  {
    path: '/graph',
    name: 'graph',
    component: () => import('@/views/GraphView.vue'),
    meta: { title: '知识图谱' },
  },
  {
    path: '/history',
    name: 'history',
    component: () => import('@/views/HistoryView.vue'),
    meta: { title: '问答历史', requiresAuth: true },
  },
  {
    path: '/favorites',
    name: 'favorites',
    component: () => import('@/views/FavoriteView.vue'),
    meta: { title: '我的收藏', requiresAuth: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  // 守卫可能在 pinia 安装前被首次触发（app.use(router) 会解析初始路由），
  // 显式传 pinia 实例，避免 "no active Pinia"。
  const auth = useAuthStore(pinia)

  if (to.matched.some((r) => r.meta.requiresAuth) && !auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 已登录再点登录页，直接回问答，避免出现"登录成功却停在登录页"
  if (to.name === 'login' && auth.isLoggedIn) {
    return { path: '/chat' }
  }
  return true
})

export default router
