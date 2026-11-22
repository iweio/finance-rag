/** Pinia 单例。`router` 的登录守卫要用它，所以从 `main.ts` 抽出来，避免 main ↔ router 循环依赖。 */
import { createPinia } from 'pinia'

export const pinia = createPinia()
