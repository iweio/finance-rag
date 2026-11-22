<template>
  <div class="chat-page">
    <header class="chat-toolbar">
      <div class="chat-toolbar__info">
        <h1>制度问答</h1>
        <p>答案只依据库内条文生成；检索不到依据时会明确拒答。</p>
      </div>
      <div class="chat-toolbar__actions">
        <el-tag v-if="IS_MOCK" size="small" type="warning" effect="plain">Mock 数据</el-tag>
        <el-button v-if="turns.length" text @click="clearAll">清空对话</el-button>
      </div>
    </header>

    <div ref="scrollRef" class="chat-thread">
      <section v-if="turns.length === 0" class="chat-hero">
        <div class="chat-hero__logo">RAG</div>
        <h2>问一个金融制度问题</h2>
        <p>系统按「条」检索 159 份现行制度文件，答案带条文出处与可信度；没有依据就不编。</p>
        <div class="chat-hero__examples">
          <button
            v-for="example in examples"
            :key="example"
            type="button"
            class="chat-hero__example"
            @click="useExample(example)"
          >
            {{ example }}
          </button>
        </div>
      </section>

      <article v-for="turn in turns" :key="turn.id" class="turn">
        <div class="turn__question">
          <span class="turn__badge">问</span>
          <p>{{ turn.question }}</p>
        </div>

        <div class="turn__answer">
          <div class="turn__answer-head">
            <span class="turn__badge turn__badge--answer">答</span>
            <ConfidenceBadge v-if="turn.score !== null" :score="turn.score" />
            <span v-if="turn.elapsed_ms !== null" class="turn__elapsed">
              {{ (turn.elapsed_ms / 1000).toFixed(1) }}s
            </span>
            <CopyButton :text="turn.answer" label="复制" class="turn__copy" />
          </div>

          <MarkdownView
            v-if="turn.answer"
            :content="turn.answer"
            :streaming="turn.status === 'streaming'"
          />
          <p v-else-if="turn.status === 'streaming'" class="turn__thinking">正在检索并生成…</p>

          <ErrorBanner
            v-if="turn.error"
            :message="turn.error"
            :code="turn.error_code"
            title="生成中断"
          >
            <template #action>
              <el-button size="small" @click="retry(turn)">重试</el-button>
            </template>
          </ErrorBanner>

          <p v-if="turn.status === 'aborted'" class="turn__aborted">已停止生成，以上内容可能不完整。</p>

          <el-alert
            v-if="turn.status === 'done' && turn.sources.length === 0"
            type="warning"
            :closable="false"
            show-icon
            title="知识库中未找到直接依据"
            description="系统按「无依据拒答」策略处理，不给出推测性答案。可以换一种问法，或到检索页用关键词查找。"
            class="turn__refusal"
          />

          <div v-if="turn.sources.length" class="turn__sources">
            <h3 class="turn__sources-title">
              溯源条文 <span>{{ turn.sources.length }}</span>
            </h3>
            <div class="turn__sources-grid">
              <SourceCard
                v-for="(source, index) in turn.sources"
                :key="`${source.clause_id}-${index}`"
                :source="source"
                :rank="index + 1"
              />
            </div>
          </div>
        </div>
      </article>
    </div>

    <footer class="chat-composer">
      <div class="chat-composer__filters">
        <FilterBar
          v-model="filters"
          :dict="meta.dict"
          :loading="meta.loading"
          compact
        />
      </div>

      <div class="chat-composer__box">
        <el-input
          v-model="question"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
          resize="none"
          placeholder="问一个金融制度问题，Enter 发送，Shift+Enter 换行"
          @keydown.enter.exact.prevent="send"
        />
        <div class="chat-composer__actions">
          <el-button v-if="streaming" type="danger" plain @click="stop">停止</el-button>
          <el-button v-else type="primary" :disabled="!question.trim()" @click="send">
            发送
          </el-button>
        </div>
      </div>
      <p class="chat-composer__hint">
        答案由检索到的条文拼接生成，请以条文原文为准；筛选项只作用于本次检索。
      </p>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'

import { api, IS_MOCK } from '@/api'
import type { ChatRequest, Filters } from '@/api/types'
import { useChatStream, type ChatStreamState } from '@/composables/useSSE'
import { useChatStore, type ChatTurn } from '@/stores/chat'
import { useFiltersStore } from '@/stores/filters'
import { useMetaStore } from '@/stores/meta'
import { useSessionStore } from '@/stores/session'

import ConfidenceBadge from '@/components/ConfidenceBadge.vue'
import CopyButton from '@/components/CopyButton.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import FilterBar from '@/components/FilterBar.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import SourceCard from '@/components/SourceCard.vue'

const chat = useChatStore()
const session = useSessionStore()
const meta = useMetaStore()
const filtersStore = useFiltersStore()

const filters = computed<Filters>({
  get: () => filtersStore.filters,
  set: (value: Filters) => filtersStore.setFilters(value),
})

const turns = computed<ChatTurn[]>(() => chat.turns)
const question = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const activeTurnId = ref<string | null>(null)

const stream = useChatStream()
const streaming = computed(() => stream.state.status === 'streaming')

const examples = [
  '商业银行核心一级资本充足率不得低于多少',
  '商业银行贷款应当遵守哪些资产负债比例管理规定',
  '征信业务中采集个人信息有什么限制',
]

function syncTurn(turnId: string, state: ChatStreamState): void {
  chat.patchTurn(turnId, {
    answer: state.answer,
    sources: state.sources,
    score: state.score,
    qa_id: state.qaId,
    elapsed_ms: state.elapsedMs,
    status: state.status === 'idle' ? 'streaming' : state.status,
    error: state.error,
    error_code: state.errorCode,
  })
}

watch(
  stream.state,
  () => {
    if (activeTurnId.value) syncTurn(activeTurnId.value, stream.state)
  },
  { deep: true },
)

function scrollToBottom(force = false): void {
  const element = scrollRef.value
  if (!element) return
  const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 180
  if (!force && !nearBottom) return
  void nextTick(() => {
    element.scrollTop = element.scrollHeight
  })
}

watch(
  () => turns.value[turns.value.length - 1]?.answer.length ?? 0,
  () => scrollToBottom(),
)

watch(
  () => turns.value.length,
  () => scrollToBottom(true),
)

async function runTurn(text: string, turnId: string): Promise<void> {
  activeTurnId.value = turnId
  const req: ChatRequest = {
    question: text,
    session_id: session.sessionId,
    filters: {
      domain: filtersStore.filters.domain ?? null,
      status: filtersStore.filters.status ?? null,
      org: filtersStore.filters.org ?? null,
    },
  }
  try {
    await stream.start((signal) => api.chatStream(req, signal))
  } finally {
    syncTurn(turnId, stream.state)
    activeTurnId.value = null
    scrollToBottom(true)
  }
}

function send(): void {
  const text = question.value.trim()
  if (!text || streaming.value) return
  question.value = ''
  const turnId = chat.createTurn(text)
  scrollToBottom(true)
  void runTurn(text, turnId)
}

function useExample(example: string): void {
  question.value = example
  send()
}

function stop(): void {
  stream.stop()
}

function retry(turn: ChatTurn): void {
  if (streaming.value) return
  chat.patchTurn(turn.id, {
    answer: '',
    sources: [],
    score: null,
    qa_id: null,
    elapsed_ms: null,
    status: 'streaming',
    error: null,
    error_code: null,
  })
  void runTurn(turn.question, turn.id)
}

function clearAll(): void {
  if (streaming.value) stream.stop()
  chat.clear()
  ElMessage.success('已清空当前对话')
}

onMounted(() => {
  void meta.load()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--rag-topbar-h) - 40px);
  min-height: 420px;
}
.chat-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
}
.chat-toolbar__info h1 {
  margin: 0;
  font-size: 17px;
  color: var(--rag-text);
}
.chat-toolbar__info p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--rag-text-3);
}
.chat-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chat-thread {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 4px 16px;
  scroll-behavior: smooth;
}
.chat-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 16px 32px;
  text-align: center;
}
.chat-hero__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--rag-primary), var(--rag-primary-600));
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.chat-hero h2 {
  margin: 0;
  font-size: 20px;
  color: var(--rag-text);
}
.chat-hero p {
  margin: 0;
  max-width: 560px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--rag-text-2);
}
.chat-hero__examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 6px;
}
.chat-hero__example {
  padding: 8px 14px;
  border: 1px solid var(--rag-border);
  border-radius: 999px;
  background: var(--rag-surface);
  color: var(--rag-text-2);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.chat-hero__example:hover {
  border-color: var(--rag-primary);
  color: var(--rag-primary);
  background: var(--rag-primary-50);
}
.turn {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 0;
  border-top: 1px solid var(--rag-border);
}
.turn:first-of-type {
  border-top: none;
}
.turn__question,
.turn__answer-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.turn__question p {
  margin: 0;
  padding-top: 2px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--rag-text);
}
.turn__badge {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--rag-primary);
  color: #fff;
  font-size: 12px;
  line-height: 1;
}
.turn__badge--answer {
  background: var(--rag-success);
}
.turn__answer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 30px;
}
.turn__elapsed {
  font-size: 12px;
  color: var(--rag-text-3);
  font-variant-numeric: tabular-nums;
}
.turn__copy {
  margin-left: auto;
}
.turn__thinking {
  margin: 0;
  font-size: 13px;
  color: var(--rag-text-3);
}
.turn__aborted {
  margin: 0;
  font-size: 12px;
  color: var(--rag-warning);
}
.turn__refusal {
  max-width: 720px;
}
.turn__sources {
  margin-top: 2px;
}
.turn__sources-title {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--rag-text-2);
}
.turn__sources-title span {
  color: var(--rag-text-3);
  font-weight: 400;
}
.turn__sources-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
}
.chat-composer {
  flex: none;
  padding-top: 8px;
  border-top: 1px solid var(--rag-border);
}
.chat-composer__filters {
  margin-bottom: 8px;
}
.chat-composer__box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}
.chat-composer__box :deep(.el-textarea) {
  flex: 1;
}
.chat-composer__actions {
  flex: none;
  padding-bottom: 2px;
}
.chat-composer__hint {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--rag-text-3);
}
@media (max-width: 768px) {
  .chat-page {
    height: calc(100vh - var(--rag-topbar-h) - 24px);
  }
  .turn__answer {
    padding-left: 0;
  }
  .turn__sources-grid {
    grid-template-columns: 1fr;
  }
  .chat-composer__box {
    flex-direction: column;
    align-items: stretch;
  }
  .chat-composer__actions {
    display: flex;
    justify-content: flex-end;
  }
}
</style>
