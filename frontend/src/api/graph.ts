/**
 * 知识图谱接口（契约/API.md §10）。
 * 服务端只读 kg/out/serving/，不依赖 torch；产物缺失时后端返回 50301。
 */
import { request } from './client'
import type { GraphStats, GraphSubgraph } from './types'

export function graphStats(): Promise<GraphStats> {
  return request<GraphStats>({ url: '/graph/stats', method: 'get' })
}

export function graphSubgraph(
  clauseId: string,
  hops = 2,
  edgeTypes?: string[],
  limit = 200,
): Promise<GraphSubgraph> {
  const params: Record<string, string | number> = {
    clause_id: clauseId,
    hops,
    limit,
  }
  if (edgeTypes && edgeTypes.length > 0) {
    params.edge_types = edgeTypes.join(',')
  }
  return request<GraphSubgraph>({ url: '/graph/subgraph', method: 'get', params })
}
