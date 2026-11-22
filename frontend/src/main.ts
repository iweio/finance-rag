/**
 * 前端入口。
 *
 * Element Plus 的**组件**由 unplugin-vue-components 按需引入（见 vite.config.ts），
 * 但 `ElMessage` / `ElMessageBox` 这类函数式 API 的样式不会被组件解析器带上，
 * 所以这里全局引一次 `element-plus/dist/index.css`：多 ~300KB，换"消息弹不出来"的坑。
 */
import { createApp } from 'vue'

import 'element-plus/dist/index.css'
import '@/styles/var.css'
import '@/styles/base.css'

import App from './App.vue'
import router from './router'
import { pinia } from './stores'
import { onUnauthorized } from './api/client'

const app = createApp(App)
app.use(pinia)
app.use(router)

// 任何请求拿到 40101（token 过期/被清）都回登录页，并记住原路径
onUnauthorized(() => {
  const current = router.currentRoute.value
  if (current.name === 'login') return
  void router.push({ path: '/login', query: { redirect: current.fullPath } })
})

app.mount('#app')
