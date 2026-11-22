<template>
  <div class="graph-page">
    <div class="graph-toolbar">
      <el-input
        v-model="focusInput"
        class="graph-toolbar__input"
        placeholder="条文 ID，如 NFRA-016-A26"
        clearable
        @keyup.enter="load"
      />
      <el-select v-model="hops" class="graph-toolbar__hops" @change="load">
        <el-option :value="1" label="1 跳" />
        <el-option :value="2" label="2 跳" />
      </el-select>
      <el-button type="primary" :loading="loading" @click="load">加载图谱</el-button>
      <span v-if="stats" class="graph-toolbar__stats">
        {{ stats.total_nodes.toLocaleString() }} 节点 · {{ stats.total_edges.toLocaleString() }} 边
        <el-tag v-if="!stats.has_embeddings" size="small" type="info" effect="plain">
          未训练 HGT（仅图）
        </el-tag>
      </span>
    </div>

    <ErrorBanner v-if="error" :message="error" :code="errorCode" title="图谱加载失败" />

    <LoadingSkeleton v-if="loading" :rows="5" />

    <template v-else-if="graph">
      <div class="graph-meta">
        <span class="graph-meta__focus">中心：{{ focusLabel }}</span>
        <span class="muted">节点 {{ graph.node_count }} · 边 {{ graph.edge_count }}</span>
        <el-tag v-if="graph.truncated" size="small" type="warning" effect="plain">
          已达节点上限，仅展示部分
        </el-tag>
      </div>

      <div class="graph-filters">
        <button
          v-for="t in edgeTypes"
          :key="t"
          type="button"
          class="chip"
          :class="{ 'is-on': !hiddenTypes.has(t) }"
          @click="toggleType(t)"
        >
          {{ edgeLabel(t) }}
          <small>{{ edgeTypeCount(t) }}</small>
        </button>
      </div>

      <div class="graph-canvas">
        <svg :viewBox="`0 0 ${W} ${H}`" class="graph-svg" role="img" aria-label="条文知识图谱">
          <g class="graph-edges">
            <line
              v-for="(e, i) in visibleEdges"
              :key="`e${i}`"
              :x1="pos(e.src).x"
              :y1="pos(e.src).y"
              :x2="pos(e.dst).x"
              :y2="pos(e.dst).y"
              :stroke="edgeColor(e.type)"
              :stroke-width="e.type === 'CITES' ? 1.8 : 1"
              :stroke-dasharray="dashed(e.type) ? '4 3' : undefined"
              stroke-opacity="0.55"
            />
          </g>
          <g class="graph-nodes">
            <g
              v-for="n in graph.nodes"
              :key="n.id"
              :transform="`translate(${pos(n.id).x},${pos(n.id).y})`"
              class="graph-node"
              :class="{ 'is-clause': n.type === 'Clause' }"
              @click="onNodeClick(n)"
            >
              <title>{{ n.label }}（{{ n.type }}）</title>
              <circle
                :r="radius(n.type)"
                :fill="color(n.type)"
                :stroke="n.id === graph.focus ? '#111827' : '#ffffff'"
                :stroke-width="n.id === graph.focus ? 2.5 : 1"
              />
              <text
                v-if="showLabel(n)"
                :y="radius(n.type) + 12"
                text-anchor="middle"
                class="graph-label"
              >
                {{ short(n.label) }}
              </text>
            </g>
          </g>
        </svg>
        <div class="graph-legend">
          <span v-for="t in nodeTypes" :key="t" class="legend-item">
            <i :style="{ background: color(t) }" />{{ nodeLabel(t) }}
          </span>
        </div>
      </div>

      <p class="graph-hint">
        点条文节点进入详情；边类型可点上方标签过滤。当前画的是 {{ hops }} 跳子图，
        全图共 {{ stats?.total_nodes.toLocaleString() ?? '—' }} 个节点，不在浏览器里一次渲染。
      </p>
    </template>

    <EmptyState
      v-else-if="!loading && !error"
      title="还没有图谱数据"
      description="输入一个条文 ID 后点击「加载图谱」"
      icon="search"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api'
import type { GraphEdge, GraphNode, GraphStats, GraphSubgraph } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import { errorCode as toCode, errorMessage } from '@/utils/error'

const W = 900
const H = 620
const DEFAULT_CLAUSE = 'NFRA-016-A26'

const NODE_COLORS: Record<string, string> = {
  Clause: '#2563eb',
  Chapter: '#7c3aed',
  Doc: '#0ea5e9',
  Agency: '#f59e0b',
  TopicTag: '#16a34a',
  ExternalLaw: '#94a3b8',
}
const NODE_LABELS: Record<string, string> = {
  Clause: '条文',
  Chapter: '章节',
  Doc: '法规',
  Agency: '机关',
  TopicTag: '标签',
  ExternalLaw: '库外法规',
}
const EDGE_COLORS: Record<string, string> = {
  NEXT: '#93c5fd',
  IN_CHAPTER: '#c4b5fd',
  IN_DOC: '#7dd3fc',
  CITES: '#ef4444',
  MENTIONS_DOC: '#14b8a6',
  MENTIONS_EXT: '#cbd5e1',
  CITES_DOC: '#0d9488',
  ISSUED_BY: '#f59e0b',
  HAS_TAG: '#22c55e',
}
const EDGE_LABELS: Record<string, string> = {
  NEXT: '相邻',
  IN_CHAPTER: '所属章节',
  IN_DOC: '所属法规',
  CITES: '引用条文',
  MENTIONS_DOC: '提及法规',
  MENTIONS_EXT: '提及库外法规',
  CITES_DOC: '法规引用',
  ISSUED_BY: '发文机关',
  HAS_TAG: '主题标签',
}
const NODE_TYPES = Object.keys(NODE_LABELS)
const DASHED = new Set(['MENTIONS_EXT', 'CITES_DOC'])

const route = useRoute()
const router = useRouter()

const focusInput = ref(String(route.query.clause_id || DEFAULT_CLAUSE))
const hops = ref(2)
const loading = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<number | null>(null)
const stats = ref<GraphStats | null>(null)
const graph = ref<GraphSubgraph | null>(null)
const hiddenTypes = ref(new Set<string>())

const edgeTypes = computed(() => {
  const set = new Set<string>()
  for (const e of graph.value?.edges ?? []) set.add(e.type)
  return [...set].sort()
})

const visibleEdges = computed(() =>
  (graph.value?.edges ?? []).filter((e) => !hiddenTypes.value.has(e.type)),
)

const focusLabel = computed(() => {
  const focus = graph.value?.focus
  const node = graph.value?.nodes.find((n) => n.id === focus)
  return node?.label ?? focus ?? '—'
})

/** 环形布局：depth 0 居中，第 d 跳排在第 d 环；同环按类型+名字排序，避免每次刷新位置乱跳 */
const layout = computed(() => {
  const map = new Map<string, { x: number; y: number }>()
  const nodes = graph.value?.nodes ?? []
  if (nodes.length === 0) return map
  const byDepth = new Map<number, GraphNode[]>()
  for (const n of nodes) {
    const d = n.depth ?? 0
    const list = byDepth.get(d)
    if (list) list.push(n)
    else byDepth.set(d, [n])
  }
  const cx = W / 2
  const cy = H / 2
  for (const [depth, list] of [...byDepth.entries()].sort((a, b) => a[0] - b[0])) {
    if (depth === 0) {
      map.set(list[0].id, { x: cx, y: cy })
      for (const extra of list.slice(1)) map.set(extra.id, { x: cx, y: cy })
      continue
    }
    const sorted = [...list].sort(
      (a, b) => NODE_TYPES.indexOf(a.type) - NODE_TYPES.indexOf(b.type) || a.label.localeCompare(b.label),
    )
    const radius = 110 + depth * 105
    sorted.forEach((n, i) => {
      const angle = (-Math.PI / 2) + (2 * Math.PI * i) / Math.max(sorted.length, 1)
      map.set(n.id, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) })
    })
  }
  return map
})

function pos(id: string) {
  return layout.value.get(id) ?? { x: W / 2, y: H / 2 }
}
function color(type: string) {
  return NODE_COLORS[type] ?? '#64748b'
}
function nodeLabel(type: string) {
  return NODE_LABELS[type] ?? type
}
function edgeColor(type: string) {
  return EDGE_COLORS[type] ?? '#94a3b8'
}
function edgeLabel(type: string) {
  return EDGE_LABELS[type] ?? type
}
function dashed(type: string) {
  return DASHED.has(type)
}
function radius(type: string) {
  if (type === 'Doc') return 11
  if (type === 'Agency') return 10
  if (type === 'Chapter') return 9
  if (type === 'TopicTag') return 8
  if (type === 'ExternalLaw') return 5
  return 7
}
function showLabel(n: GraphNode) {
  if (n.id === graph.value?.focus) return true
  return n.type !== 'Clause' && n.type !== 'ExternalLaw'
}
function short(label: string) {
  return label.length > 14 ? `${label.slice(0, 14)}…` : label
}
function edgeTypeCount(type: string) {
  return (graph.value?.edges ?? []).filter((e) => e.type === type).length
}
function toggleType(type: string) {
  const next = new Set(hiddenTypes.value)
  if (next.has(type)) next.delete(type)
  else next.add(type)
  hiddenTypes.value = next
}
function onNodeClick(n: GraphNode) {
  if (n.type !== 'Clause') return
  const clauseId = n.id.startsWith('C:') ? n.id.slice(2) : n.id
  void router.push(`/clause/${encodeURIComponent(clauseId)}`)
}

async function load() {
  const cid = focusInput.value.trim()
  if (!cid) return
  loading.value = true
  error.value = null
  errorCode.value = null
  try {
    const [st, sub] = await Promise.all([
      api.graphStats().catch(() => null),
      api.graphSubgraph(cid, hops.value, undefined, 200),
    ])
    stats.value = st
    graph.value = sub
    hiddenTypes.value = new Set()
    if (String(route.query.clause_id || '') !== cid) {
      void router.replace({ path: '/graph', query: { ...route.query, clause_id: cid } })
    }
  } catch (e: unknown) {
    graph.value = null
    error.value = errorMessage(e, '图谱加载失败')
    errorCode.value = toCode(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => route.query.clause_id,
  (v) => {
    const cid = String(v || '')
    if (cid && cid !== focusInput.value) {
      focusInput.value = cid
      void load()
    }
  },
)
</script>

<style scoped>
.graph-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.graph-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.graph-toolbar__input {
  width: 280px;
}
.graph-toolbar__hops {
  width: 96px;
}
.graph-toolbar__stats {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--rag-text-3);
  font-size: 12px;
}
.graph-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--rag-text-2);
}
.graph-meta__focus {
  font-weight: 600;
  color: var(--rag-text);
}
.graph-filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.chip {
  border: 1px solid var(--rag-border);
  background: var(--rag-surface);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--rag-text-2);
  cursor: pointer;
}
.chip small {
  opacity: 0.65;
  margin-left: 4px;
}
.chip.is-on {
  border-color: var(--rag-primary-200);
  background: var(--rag-primary-50);
  color: var(--rag-primary-600);
}
.graph-canvas {
  background: var(--rag-surface);
  border: 1px solid var(--rag-border);
  border-radius: var(--rag-radius);
  box-shadow: var(--rag-shadow-sm);
  padding: 8px;
  position: relative;
}
.graph-svg {
  width: 100%;
  height: auto;
  display: block;
}
.graph-node {
  cursor: default;
}
.graph-node.is-clause {
  cursor: pointer;
}
.graph-node.is-clause:hover circle {
  stroke: var(--rag-primary);
  stroke-width: 3;
}
.graph-label {
  font-size: 10px;
  fill: var(--rag-text-2);
  pointer-events: none;
}
.graph-legend {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  padding: 6px 10px 2px;
  font-size: 12px;
  color: var(--rag-text-3);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.legend-item i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.graph-hint {
  margin: 0;
  font-size: 12px;
  color: var(--rag-text-3);
}
.muted {
  color: var(--rag-text-3);
}
@media (max-width: 767px) {
  .graph-toolbar__input {
    width: 100%;
  }
  .graph-toolbar__stats {
    margin-left: 0;
  }
}
</style>
