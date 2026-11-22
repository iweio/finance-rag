<template>
  <div class="not-found">
    <p class="not-found__code">404</p>
    <h1>页面不存在</h1>
    <p class="not-found__desc">链接可能已经失效，或者地址输错了。</p>
    <div class="not-found__actions">
      <el-button type="primary" @click="router.push('/chat')">回到问答</el-button>
      <el-button @click="router.push('/search')">去检索</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 60vh;
  text-align: center;
}
.not-found__code {
  margin: 0;
  font-size: 64px;
  font-weight: 700;
  line-height: 1;
  background: linear-gradient(135deg, var(--rag-primary), var(--rag-primary-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.not-found h1 {
  margin: 8px 0 0;
  font-size: 18px;
  color: var(--rag-text);
}
.not-found__desc {
  margin: 0;
  font-size: 13px;
  color: var(--rag-text-3);
}
.not-found__actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
</style>
