<template>
  <el-tooltip :content="copied ? '已复制' : tip" placement="top" :disabled="disabled">
    <span class="copy-button">
      <el-button text size="small" :disabled="disabled || !text" @click="copy">
        <el-icon><DocumentCopy /></el-icon>
        <span class="copy-button__label">{{ copied ? '已复制' : label }}</span>
      </el-button>
    </span>
  </el-tooltip>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

const props = withDefaults(
  defineProps<{
    text: string
    label?: string
    tip?: string
    disabled?: boolean
  }>(),
  { label: '复制', tip: '复制到剪贴板', disabled: false },
)

const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

function fallbackCopy(text: string): void {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'readonly')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(textarea)
  }
}

async function copy(): Promise<void> {
  if (!props.text) return
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(props.text)
    } else {
      fallbackCopy(props.text)
    }
    copied.value = true
    ElMessage.success('已复制')
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    fallbackCopy(props.text)
    copied.value = true
  }
}

onBeforeUnmount(() => {
  if (timer !== null) clearTimeout(timer)
})
</script>

<style scoped>
.copy-button {
  display: inline-flex;
}
.copy-button__label {
  margin-left: 2px;
}
</style>
