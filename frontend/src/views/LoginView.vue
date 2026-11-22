<template>
  <div class="login-page">
    <div class="login-card">
      <header class="login-card__head">
        <div class="login-card__logo">RAG</div>
        <h1>金融制度知识库</h1>
        <p>条文级检索 · 答案溯源 · 无依据拒答</p>
      </header>

      <el-tabs v-model="tab" class="login-tabs" @tab-change="onTabChange">
        <el-tab-pane label="登录" name="login">
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="rules"
            label-position="top"
            @submit.prevent="submitLogin"
          >
            <el-form-item label="用户名" prop="username">
              <el-input
                v-model="loginForm.username"
                placeholder="请输入用户名"
                autocomplete="username"
                clearable
              />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="请输入密码"
                autocomplete="current-password"
                show-password
                @keyup.enter="submitLogin"
              />
            </el-form-item>

            <ErrorBanner v-if="error" :message="error" :code="errorCode" title="登录失败" />

            <el-button
              type="primary"
              class="login-card__submit"
              :loading="loading"
              @click="submitLogin"
            >
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="注册" name="register">
          <el-form
            ref="registerFormRef"
            :model="registerForm"
            :rules="registerRules"
            label-position="top"
            @submit.prevent="submitRegister"
          >
            <el-form-item label="用户名" prop="username">
              <el-input
                v-model="registerForm.username"
                placeholder="3~32 位字母、数字、下划线或汉字"
                autocomplete="username"
                clearable
              />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="registerForm.password"
                type="password"
                placeholder="至少 6 位"
                autocomplete="new-password"
                show-password
              />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm">
              <el-input
                v-model="registerForm.confirm"
                type="password"
                placeholder="再输一遍密码"
                autocomplete="new-password"
                show-password
                @keyup.enter="submitRegister"
              />
            </el-form-item>

            <ErrorBanner v-if="error" :message="error" :code="errorCode" title="注册失败" />

            <el-button
              type="primary"
              class="login-card__submit"
              :loading="loading"
              @click="submitRegister"
            >
              注册并登录
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <p v-if="IS_MOCK" class="login-card__hint">
        Mock 模式演示账号：<code>demo</code> / <code>demo123</code>（注册的账号只存在内存，刷新后需重注册）
      </p>

      <button type="button" class="login-card__back" @click="goHome">先随便看看 →</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

import { IS_MOCK } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { errorCode as toCode, errorMessage, isApiError } from '@/utils/error'

import ErrorBanner from '@/components/ErrorBanner.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const tab = ref<'login' | 'register'>('login')
const loading = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<number | null>(null)

const loginFormRef = ref<FormInstance>()
const registerFormRef = ref<FormInstance>()

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ username: '', password: '', confirm: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
}

const registerRules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '用户名需 3~32 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请设置密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再输一遍密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string, callback: (err?: Error) => void) => {
        if (value !== registerForm.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

const redirectTarget = computed<string>(() => {
  const raw = route.query.redirect
  const target = typeof raw === 'string' ? raw : ''
  // 只允许站内相对路径，避免被 redirect 到外站
  if (target.startsWith('/') && !target.startsWith('//')) return target
  return '/chat'
})

function resetError(): void {
  error.value = null
  errorCode.value = null
}

function onTabChange(): void {
  resetError()
}

watch(tab, resetError)

async function submitLogin(): Promise<void> {
  if (!loginFormRef.value || loading.value) return
  const valid = await loginFormRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  resetError()
  try {
    await auth.login(loginForm.username, loginForm.password)
    ElMessage.success('登录成功')
    router.replace(redirectTarget.value)
  } catch (e: unknown) {
    error.value = errorMessage(e, '登录失败，请稍后重试')
    errorCode.value = toCode(e)
    if (isApiError(e) && e.code === 40101) {
      error.value = '用户名或密码错误'
    }
  } finally {
    loading.value = false
  }
}

async function submitRegister(): Promise<void> {
  if (!registerFormRef.value || loading.value) return
  const valid = await registerFormRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  resetError()
  try {
    await auth.register(registerForm.username, registerForm.password)
    await auth.login(registerForm.username, registerForm.password)
    ElMessage.success('注册成功，已自动登录')
    router.replace(redirectTarget.value)
  } catch (e: unknown) {
    error.value = errorMessage(e, '注册失败，请稍后重试')
    errorCode.value = toCode(e)
    if (isApiError(e) && e.code === 40901) {
      error.value = '用户名已存在，请直接登录或换一个用户名'
    }
  } finally {
    loading.value = false
  }
}

function goHome(): void {
  router.push('/chat')
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px 16px;
  background:
    radial-gradient(1200px 600px at 15% 10%, var(--rag-primary-50), transparent 60%),
    radial-gradient(900px 500px at 85% 90%, var(--rag-accent-50), transparent 60%),
    var(--rag-bg);
}
.login-card {
  width: 100%;
  max-width: 400px;
  padding: 28px 26px 22px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: 16px;
  box-shadow: var(--rag-shadow);
}
.login-card__head {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-bottom: 18px;
  text-align: center;
}
.login-card__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 4px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--rag-primary), var(--rag-primary-600));
  color: #fff;
  font-weight: 700;
  letter-spacing: 1px;
}
.login-card__head h1 {
  margin: 0;
  font-size: 19px;
  color: var(--rag-text);
}
.login-card__head p {
  margin: 0;
  font-size: 12px;
  color: var(--rag-text-3);
}
.login-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: var(--rag-border);
}
.login-card__submit {
  width: 100%;
  margin-top: 4px;
}
.login-card__hint {
  margin: 16px 0 0;
  padding: 8px 10px;
  border-radius: var(--rag-radius-sm);
  background: var(--rag-fill);
  font-size: 12px;
  line-height: 1.7;
  color: var(--rag-text-2);
}
.login-card__hint code {
  padding: 0 4px;
  border-radius: 3px;
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
}
.login-card__back {
  display: block;
  width: 100%;
  margin-top: 14px;
  border: none;
  background: none;
  color: var(--rag-text-3);
  font-size: 12px;
  cursor: pointer;
}
.login-card__back:hover {
  color: var(--rag-primary);
}
</style>
