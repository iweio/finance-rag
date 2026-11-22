/**
 * 前端唯一的接口字段定义处。
 *
 * 硬规矩：字段名与 `契约/API.md` **逐字一致**，一律 snake_case，禁止在前端重命名成驼峰。
 * 日期字段是 int `YYYYMMDD`；`history.created_at` 是 14 位 int `YYYYMMDDHHMMSS`，
 * 与日期字段不是一回事，别混用格式化函数。
 */

/** 统一响应包（chat 的 SSE 除外） */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export type RelationType = '同条' | '同章' | '引用' | '相似'

/** API.md §1.1 / §4：sources[] 与 related[] 共用 */
export interface ClauseBrief {
  clause_id: string
  doc_id: string
  doc_title: string
  clause_no: string
  clause_index: number
  chapter: string | null
  snippet: string
  score: number
  status: string
  effective_date: number | null
  source_url: string | null
}

/** API.md §4：ClauseBrief + relation_type */
export interface RelatedClause extends ClauseBrief {
  relation_type: RelationType | string
}

/** API.md §3：条文详情，17 个字段 */
export interface ClauseDetail {
  clause_id: string
  doc_id: string
  doc_title: string
  clause_no: string
  clause_index: number
  chapter: string | null
  text: string
  doc_type: string
  domain: string
  org: string
  doc_number: string | null
  publish_date: number | null
  effective_date: number | null
  expire_date: number | null
  status: string
  source_url: string | null
  fetched_at: number
}

/** API.md §2：检索结果精简结构（比 ClauseBrief 少 4 个、多 highlight） */
export interface SearchItem {
  clause_id: string
  doc_title: string
  clause_no: string
  snippet: string
  highlight: string
  status: string
  source_url: string | null
  score: number
}

export interface SearchResult {
  total: number
  page: number
  size: number
  items: SearchItem[]
}

/** 查询筛选：契约里只有 3 个字段，topic_tags / doc_type 不参与请求（裁决 #1） */
export interface Filters {
  domain?: string | null
  status?: string | null
  org?: string | null
}

export interface FilterOption {
  value: string
  label: string
  count: number
}

export interface DateRange {
  min: number | null
  max: number | null
}

/** API.md §5 */
export interface FilterDict {
  domain: FilterOption[]
  status: FilterOption[]
  org: FilterOption[]
  doc_type: FilterOption[]
  topic_tags: FilterOption[]
  date_range: DateRange
}

/** API.md §6 */
export interface HistorySource {
  clause_id: string
  clause_no: string
  doc_title: string
}

export interface HistoryItem {
  qa_id: number
  question: string
  answer: string
  score: number | null
  created_at: number
  sources: HistorySource[]
}

export interface HistoryPage {
  total: number
  page: number
  size: number
  items: HistoryItem[]
}

/** API.md §7：`created_at` 与 /history 同口径，14 位 int `YYYYMMDDHHMMSS` */
export interface FavoriteItem {
  favorite_id: number
  clause_id: string
  doc_title: string
  clause_no: string
  created_at: number
}

export interface FavoritePage {
  total: number
  page: number
  size: number
  items: FavoriteItem[]
}

/** API.md §8 */
export interface DependencyStatus {
  status: string
  latency_ms: number
  error?: string
}

export interface MilvusStatus extends DependencyStatus {
  collection: string
  row_count: number
}

export interface Health {
  service: string
  mysql: DependencyStatus
  milvus: MilvusStatus
  version: string
  /** 后端 mock 引擎自报的模式；真实库不返回该字段 */
  mode?: string
}

/** 认证扩展（计划 §9 #7，契约 8 → 10 个接口） */
export interface AuthUser {
  user_id: number
  username: string
}

export interface AuthToken {
  token: string
  user_id: number
  username: string
  expires_in: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}

/** POST /chat 请求体 */
export interface ChatRequest {
  question: string
  session_id: string
  filters?: Filters | null
}

/** SSE 事件：契约 4 类 + 扩展的 error（裁决 #6） */
export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'sources'; sources: ClauseBrief[] }
  | { type: 'score'; score: number }
  | { type: 'done'; qa_id: number; elapsed_ms: number }
  | { type: 'error'; code: number; message: string }

export interface SearchRequest {
  keyword: string
  page: number
  size: number
  filters?: Filters | null
}

export interface HistoryQuery {
  session_id: string
  page?: number
  size?: number
}

export interface DeleteHistoryParams {
  session_id?: string
  qa_id?: number
}

export interface FavoriteQuery {
  page?: number
  size?: number
  clause_id?: string
}

export interface FavoriteCreate {
  clause_id?: string
  qa_id?: number
}

export interface DeleteResult {
  deleted: number
}

export interface FavoriteCreateResult {
  favorite_id: number
}

export interface RelatedResult {
  clause_id: string
  related: RelatedClause[]
}

/** 知识图谱（API.md §10）：六类节点、十类边 */
export type GraphNodeType =
  | 'Clause'
  | 'Chapter'
  | 'Doc'
  | 'Agency'
  | 'TopicTag'
  | 'ExternalLaw'

export interface GraphNode {
  id: string
  type: GraphNodeType | string
  label: string
  depth: number
  props: Record<string, unknown>
}

export interface GraphEdge {
  src: string
  type: string
  dst: string
}

export interface GraphSubgraph {
  clause_id: string
  focus: string
  hops: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
  node_count: number
  edge_count: number
}

export interface GraphStats {
  version: string
  generated_at?: string | null
  node_counts: Record<string, number>
  edge_counts: Record<string, number>
  dim: number
  has_embeddings: boolean
  total_nodes: number
  total_edges: number
}

/**
 * 真实实现与 Mock 实现的统一接口。
 * 页面只依赖 `api` 对象，不感知 VITE_USE_MOCK 走的是哪一份。
 */
export interface RagApi {
  login(req: LoginRequest): Promise<AuthToken>
  register(req: RegisterRequest): Promise<AuthUser>
  chatStream(req: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent>
  search(req: SearchRequest): Promise<SearchResult>
  getClause(clauseId: string): Promise<ClauseDetail>
  getRelated(clauseId: string, topK?: number): Promise<RelatedResult>
  getFilters(): Promise<FilterDict>
  getHistory(query: HistoryQuery): Promise<HistoryPage>
  deleteHistory(params: DeleteHistoryParams): Promise<DeleteResult>
  getFavorites(query: FavoriteQuery): Promise<FavoritePage>
  createFavorite(payload: FavoriteCreate): Promise<FavoriteCreateResult>
  deleteFavorite(favoriteId: number): Promise<DeleteResult>
  getHealth(): Promise<Health>
  graphStats(): Promise<GraphStats>
  graphSubgraph(
    clauseId: string,
    hops?: number,
    edgeTypes?: string[],
    limit?: number,
  ): Promise<GraphSubgraph>
}
