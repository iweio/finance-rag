/**
 * Mock 实现：与真实实现同一 `RagApi` 接口，`VITE_USE_MOCK=true` 时由 api/index.ts 选用。
 *
 * 数据全部来自 `fixtures.json`（真实语料切片，55KB，**不要改、不要重新生成**）。
 * 已知的 mock 限制（写在这里，免得被当成真机行为）：
 * - `SearchItem` 没有 domain / org 字段，所以 mock 的 search 只能按 status 过滤；
 * - `getClause` 对 fixtures 里没有详情的条文，用 search_item 拼一个"字段不全"的详情
 *   （doc_type/domain/org 为空、三个日期为 null），真机上这类 id 会返回 40401；
 * - 注册/登录只存在内存里，刷新页面后非 demo 账号需要重新注册；
 * - 历史/收藏需要登录（无 token 抛 40101），用来把登录守卫真的走一遍。
 */
import { getToken } from '@/api/client'
import type {
  AuthToken,
  AuthUser,
  ChatEvent,
  ChatRequest,
  ClauseDetail,
  DeleteHistoryParams,
  DeleteResult,
  FavoriteCreate,
  FavoriteCreateResult,
  FavoriteItem,
  FavoritePage,
  FavoriteQuery,
  FilterDict,
  GraphEdge,
  GraphNode,
  GraphStats,
  GraphSubgraph,
  Health,
  HistoryItem,
  HistoryPage,
  HistoryQuery,
  LoginRequest,
  RagApi,
  RegisterRequest,
  RelatedClause,
  RelatedResult,
  SearchItem,
  SearchRequest,
  SearchResult,
} from '@/api/types'
import { ApiError } from '@/utils/error'

import fixtures from './fixtures.json'
import { mockChatStream, sleep } from './stream'

interface MockFixtures {
  filters: FilterDict
  details: ClauseDetail[]
  search_items: SearchItem[]
  related: RelatedClause[]
  history: HistoryItem[]
  favorites: FavoriteItem[]
  health: Health
}

const F = fixtures as unknown as MockFixtures

// ---- 可变状态（内存副本，不动 fixtures 原对象）----
const historyState: HistoryItem[] = F.history.map((item) => ({
  ...item,
  sources: Array.isArray(item.sources) ? [...item.sources] : [],
}))
const favoriteState: FavoriteItem[] = F.favorites.map((item) => ({ ...item }))
let nextFavoriteId = favoriteState.reduce((max, item) => Math.max(max, item.favorite_id), 0) + 1
let nextUserId = 1000
const registeredUsers = new Map<string, { userId: number; password: string }>()

const DEMO_USERNAME = 'demo'
const DEMO_PASSWORD = 'demo123'

// ---- JWT（只为了前端能解出 exp；签名是假的）----
function encodeBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function decodeBase64Url(part: string): string | null {
  const normalized = part.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
  try {
    const binary = atob(padded)
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    return new TextDecoder().decode(bytes)
  } catch {
    return null
  }
}

function makeToken(userId: number, username: string): string {
  const exp = Math.floor(Date.now() / 1000) + 24 * 3600
  const header = encodeBase64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = encodeBase64Url(JSON.stringify({ sub: String(userId), username, exp }))
  return `${header}.${payload}.mock-signature`
}

interface TokenPayload {
  sub?: unknown
  username?: unknown
  exp?: unknown
}

function readTokenPayload(): TokenPayload | null {
  const token = getToken()
  if (!token) return null
  const part = token.split('.')[1]
  if (!part) return null
  const json = decodeBase64Url(part)
  if (!json) return null
  try {
    return JSON.parse(json) as TokenPayload
  } catch {
    return null
  }
}

function currentUser(): { userId: number; username: string } | null {
  const payload = readTokenPayload()
  if (!payload) return null
  if (typeof payload.exp === 'number' && payload.exp * 1000 <= Date.now()) return null
  return {
    userId: Number(payload.sub ?? 0) || 0,
    username: String(payload.username ?? ''),
  }
}

function requireUser(): { userId: number; username: string } {
  const user = currentUser()
  if (!user) throw new ApiError(40101, '未登录或登录已过期')
  return user
}

// ---- 工具 ----
function clampSize(size: number | undefined, fallback = 10): number {
  if (typeof size !== 'number' || Number.isNaN(size)) return fallback
  return Math.max(1, Math.min(50, Math.trunc(size)))
}

function clampPage(page: number | undefined): number {
  if (typeof page !== 'number' || Number.isNaN(page)) return 1
  return Math.max(1, Math.trunc(page))
}

/** 与后端同口径：14 位 int `YYYYMMDDHHMMSS`（不是字符串、不是毫秒） */
function nowStamp(): number {
  const d = new Date()
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n))
  return Number(
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
      `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`,
  )
}

function deriveDocId(clauseId: string): string {
  const match = /^([A-Z]+-\d{3})/.exec(clauseId)
  return match ? match[1] : ''
}

function clauseIndexOf(clauseId: string): number {
  const match = /-A(\d+)$/.exec(clauseId)
  return match ? Number(match[1]) : 0
}

function findMetaByClauseId(clauseId: string): { docTitle: string; clauseNo: string } | null {
  const detail = F.details.find((item) => item.clause_id === clauseId)
  if (detail) return { docTitle: detail.doc_title, clauseNo: detail.clause_no }
  const item = F.search_items.find((row) => row.clause_id === clauseId)
  if (item) return { docTitle: item.doc_title, clauseNo: item.clause_no }
  const related = F.related.find((row) => row.clause_id === clauseId)
  if (related) return { docTitle: related.doc_title, clauseNo: related.clause_no }
  return null
}

// ---- 认证 ----
async function login(req: LoginRequest): Promise<AuthToken> {
  await sleep(160)
  const username = (req.username || '').trim()
  if (!username || !req.password) throw new ApiError(40001, '用户名和密码不能为空')

  const expected =
    username === DEMO_USERNAME ? DEMO_PASSWORD : registeredUsers.get(username)?.password
  if (!expected || expected !== req.password) {
    throw new ApiError(40101, '用户名或密码错误')
  }
  const userId = username === DEMO_USERNAME ? 1 : (registeredUsers.get(username)?.userId ?? 0)
  return {
    token: makeToken(userId, username),
    user_id: userId,
    username,
    expires_in: 24 * 3600,
  }
}

async function register(req: RegisterRequest): Promise<AuthUser> {
  await sleep(160)
  const username = (req.username || '').trim()
  if (!/^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$/.test(username)) {
    throw new ApiError(40001, '用户名需 3~32 位字母、数字、下划线或汉字')
  }
  if (!req.password || req.password.length < 6) {
    throw new ApiError(40001, '密码至少 6 位')
  }
  if (username === DEMO_USERNAME || registeredUsers.has(username)) {
    throw new ApiError(40901, '用户名已存在')
  }
  const userId = nextUserId
  nextUserId += 1
  registeredUsers.set(username, { userId, password: req.password })
  return { user_id: userId, username }
}

// ---- 检索 ----
async function search(req: SearchRequest): Promise<SearchResult> {
  await sleep(200)
  const keyword = (req.keyword || '').trim()
  if (!keyword) throw new ApiError(40001, '关键词不能为空')
  if (keyword.length > 100) throw new ApiError(40001, '关键词不能超过 100 字')

  let items = F.search_items.filter(
    (item) =>
      item.doc_title.includes(keyword) ||
      item.snippet.includes(keyword) ||
      item.clause_no.includes(keyword),
  )
  // SearchItem 没有 domain/org，mock 只能模拟 status 过滤
  const status = req.filters?.status
  if (status) items = items.filter((item) => item.status === status)

  const page = clampPage(req.page)
  const size = clampSize(req.size)
  const start = (page - 1) * size
  return { total: items.length, page, size, items: items.slice(start, start + size) }
}

// ---- 条文 ----
function partialDetail(item: SearchItem): ClauseDetail {
  return {
    clause_id: item.clause_id,
    doc_id: deriveDocId(item.clause_id),
    doc_title: item.doc_title,
    clause_no: item.clause_no,
    clause_index: clauseIndexOf(item.clause_id),
    chapter: null,
    text: item.snippet,
    doc_type: '',
    domain: '',
    org: '',
    doc_number: null,
    publish_date: null,
    effective_date: null,
    expire_date: null,
    status: item.status,
    source_url: item.source_url,
    fetched_at: 0,
  }
}

async function getClause(clauseId: string): Promise<ClauseDetail> {
  await sleep(160)
  const detail = F.details.find((item) => item.clause_id === clauseId)
  if (detail) return { ...detail }
  const hit = F.search_items.find((item) => item.clause_id === clauseId)
  if (hit) return partialDetail(hit)
  throw new ApiError(40401, `条文 ${clauseId} 不存在`)
}

async function getRelated(clauseId: string, topK = 5): Promise<RelatedResult> {
  await sleep(160)
  const known =
    F.details.some((item) => item.clause_id === clauseId) ||
    F.search_items.some((item) => item.clause_id === clauseId)
  if (!known) throw new ApiError(40401, `条文 ${clauseId} 不存在`)

  // fixtures 的 related 是围绕两个「枢纽条文」造的全类型样例；其余条文返回空（真机会按 doc 查）
  const hubs = new Set(['NFRA-016-A26', 'NPC-006-A39'])
  if (!hubs.has(clauseId)) return { clause_id: clauseId, related: [] }

  const limit = Math.max(1, Math.min(20, topK))
  return {
    clause_id: clauseId,
    related: F.related.filter((item) => item.clause_id !== clauseId).slice(0, limit),
  }
}

// ---- 筛选字典 ----
async function getFilters(): Promise<FilterDict> {
  await sleep(120)
  return JSON.parse(JSON.stringify(F.filters)) as FilterDict
}

// ---- 历史 ----
async function getHistory(query: HistoryQuery): Promise<HistoryPage> {
  await sleep(160)
  requireUser()
  if (!query.session_id) throw new ApiError(40001, 'session_id 不能为空')

  const page = clampPage(query.page)
  const size = clampSize(query.size)
  const start = (page - 1) * size
  return {
    total: historyState.length,
    page,
    size,
    items: historyState.slice(start, start + size),
  }
}

async function deleteHistory(params: DeleteHistoryParams): Promise<DeleteResult> {
  await sleep(160)
  requireUser()
  const hasSession = typeof params.session_id === 'string' && params.session_id.length > 0
  const hasQa = typeof params.qa_id === 'number' && Number.isFinite(params.qa_id)
  if (hasSession === hasQa) throw new ApiError(40001, 'session_id 与 qa_id 必须且只能传一个')

  if (hasQa) {
    const index = historyState.findIndex((item) => item.qa_id === params.qa_id)
    if (index < 0) return { deleted: 0 }
    historyState.splice(index, 1)
    return { deleted: 1 }
  }

  const deleted = historyState.length
  historyState.splice(0, historyState.length)
  return { deleted }
}

// ---- 收藏 ----
async function getFavorites(query: FavoriteQuery = {}): Promise<FavoritePage> {
  await sleep(160)
  requireUser()

  const page = clampPage(query.page)
  const size = clampSize(query.size)
  const filtered = query.clause_id
    ? favoriteState.filter((item) => item.clause_id === query.clause_id)
    : favoriteState
  const start = (page - 1) * size
  return {
    total: filtered.length,
    page,
    size,
    items: filtered.slice(start, start + size),
  }
}

async function createFavorite(payload: FavoriteCreate): Promise<FavoriteCreateResult> {
  await sleep(140)
  requireUser()

  const hasClause = typeof payload.clause_id === 'string' && payload.clause_id.length > 0
  const hasQa = typeof payload.qa_id === 'number' && Number.isFinite(payload.qa_id)
  if (!hasClause && !hasQa) throw new ApiError(40001, 'clause_id 与 qa_id 至少给一个')

  let clauseId = hasClause ? (payload.clause_id as string) : ''
  if (!clauseId && hasQa) {
    const historyItem = historyState.find((item) => item.qa_id === payload.qa_id)
    clauseId = historyItem?.sources?.[0]?.clause_id ?? ''
  }

  const existing = clauseId
    ? favoriteState.find((item) => item.clause_id === clauseId)
    : undefined
  if (existing) return { favorite_id: existing.favorite_id }

  if (!clauseId) throw new ApiError(40001, '无法从该问答定位到条文，请改用 clause_id 收藏')

  const meta = findMetaByClauseId(clauseId)
  const favorite: FavoriteItem = {
    favorite_id: nextFavoriteId,
    clause_id: clauseId,
    doc_title: meta?.docTitle ?? '未收录',
    clause_no: meta?.clauseNo ?? '—',
    created_at: nowStamp(),
  }
  nextFavoriteId += 1
  favoriteState.push(favorite)
  return { favorite_id: favorite.favorite_id }
}

async function deleteFavorite(favoriteId: number): Promise<DeleteResult> {
  await sleep(140)
  requireUser()
  const index = favoriteState.findIndex((item) => item.favorite_id === favoriteId)
  if (index < 0) return { deleted: 0 }
  favoriteState.splice(index, 1)
  return { deleted: 1 }
}

// ---- 健康检查 ----
async function getHealth(): Promise<Health> {
  await sleep(120)
  return JSON.parse(JSON.stringify(F.health)) as Health
}

// ---- 知识图谱（mock）----
/** 用 details/related 拼一张小图：Clause + Doc + Chapter，结构边 + 引用边。 */
function buildMockGraph(
  focusInput: string,
  hops: number,
  edgeTypes?: string[],
  limit = 200,
): GraphSubgraph {
  const focusClause = focusInput.startsWith('C:') ? focusInput.slice(2) : focusInput
  const nodes = new Map<string, GraphNode>()
  const edges: GraphEdge[] = []

  const addNode = (n: GraphNode) => {
    if (!nodes.has(n.id)) nodes.set(n.id, n)
  }
  const addEdge = (src: string, type: string, dst: string) => {
    if (!edges.some((e) => e.src === src && e.type === type && e.dst === dst)) {
      edges.push({ src, type, dst })
    }
  }

  for (const d of F.details) {
    const cid = `C:${d.clause_id}`
    addNode({
      id: cid, type: 'Clause', label: d.clause_no, depth: 0,
      props: { doc_id: d.doc_id, doc_title: d.doc_title, clause_no: d.clause_no,
        clause_index: d.clause_index, chapter: d.chapter, status: d.status },
    })
    const did = `D:${d.doc_id}`
    addNode({ id: did, type: 'Doc', label: d.doc_title, depth: 0,
      props: { doc_id: d.doc_id, doc_type: d.doc_type, domain: d.domain, org: d.org } })
    addEdge(cid, 'IN_DOC', did)
    if (d.chapter) {
      const chid = `CH:${d.doc_id}#${d.chapter}`
      addNode({ id: chid, type: 'Chapter', label: d.chapter, depth: 0,
        props: { doc_id: d.doc_id, chapter: d.chapter } })
      addEdge(cid, 'IN_CHAPTER', chid)
    }
  }

  // NEXT：同文档相邻条
  for (const d of F.details) {
    const next = F.details.find(
      (x) => x.doc_id === d.doc_id && x.clause_index === d.clause_index + 1,
    )
    if (next) addEdge(`C:${d.clause_id}`, 'NEXT', `C:${next.clause_id}`)
  }
  // 引用：related 里 relation_type=引用 的样本
  for (const r of F.related) {
    if (r.relation_type === '引用' && r.clause_id !== focusClause) {
      addEdge(`C:${focusClause}`, 'CITES', `C:${r.clause_id}`)
    }
  }

  const allowed = edgeTypes && edgeTypes.length ? new Set(edgeTypes) : null
  const adj = new Map<string, GraphEdge[]>()
  const pushAdj = (id: string, e: GraphEdge) => {
    const list = adj.get(id)
    if (list) list.push(e)
    else adj.set(id, [e])
  }
  for (const e of edges) {
    if (allowed && !allowed.has(e.type)) continue
    pushAdj(e.src, e)
    pushAdj(e.dst, { src: e.dst, type: e.type, dst: e.src })
  }

  const focus = `C:${focusClause}`
  const visited = new Map<string, number>([[focus, 0]])
  let frontier = [focus]
  let truncated = false
  const keptEdges: GraphEdge[] = []
  const seen = new Set<string>()
  for (let depth = 1; depth <= hops; depth += 1) {
    const next: string[] = []
    for (const node of frontier) {
      for (const e of adj.get(node) ?? []) {
        const key = `${e.src}|${e.type}|${e.dst}`
        if (!seen.has(key)) {
          seen.add(key)
          keptEdges.push({ src: e.src, type: e.type, dst: e.dst })
        }
        if (!visited.has(e.dst)) {
          if (visited.size >= limit) { truncated = true; continue }
          visited.set(e.dst, depth)
          next.push(e.dst)
        }
      }
    }
    frontier = next
  }

  const outNodes = [...visited.entries()].map(([id, depth]) => {
    const n = nodes.get(id)
    return n ? { ...n, depth } : { id, type: 'Clause', label: id, depth, props: {} }
  })
  const outEdges = keptEdges.filter((e) => visited.has(e.src) && visited.has(e.dst))
  return {
    clause_id: focusInput, focus, hops, nodes: outNodes, edges: outEdges,
    truncated, node_count: outNodes.length, edge_count: outEdges.length,
  }
}

async function graphStats(): Promise<GraphStats> {
  await sleep(120)
  const nodeCounts: Record<string, number> = {}
  const edgeCounts: Record<string, number> = {}
  for (const d of F.details) {
    nodeCounts.Clause = (nodeCounts.Clause ?? 0) + 1
    if (d.chapter) nodeCounts.Chapter = (nodeCounts.Chapter ?? 0) + 1
  }
  nodeCounts.Doc = new Set(F.details.map((d) => d.doc_id)).size
  for (const r of F.related) edgeCounts[r.relation_type] = (edgeCounts[r.relation_type] ?? 0) + 1
  return {
    version: 'kg-mock', generated_at: null, node_counts: nodeCounts, edge_counts: edgeCounts,
    dim: 1024, has_embeddings: false,
    total_nodes: Object.values(nodeCounts).reduce((a, b) => a + b, 0),
    total_edges: Object.values(edgeCounts).reduce((a, b) => a + b, 0),
  }
}

async function graphSubgraph(
  clauseId: string,
  hops = 2,
  edgeTypes?: string[],
  limit = 200,
): Promise<GraphSubgraph> {
  await sleep(160)
  return buildMockGraph(clauseId, hops, edgeTypes, limit)
}

export const mockApi: RagApi = {
  login,
  register,
  chatStream: (req: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> =>
    mockChatStream(req, signal),
  search,
  getClause,
  getRelated,
  getFilters,
  getHistory,
  deleteHistory,
  getFavorites,
  createFavorite,
  deleteFavorite,
  getHealth,
  graphStats,
  graphSubgraph,
}
