"""图谱查询服务：只读 `kg/out/serving/` 的导出产物，不依赖 Milvus/torch。

把这里和 `main.py` 分开的原因：本模块只用 stdlib + json，
可以在没有 fastapi 的机器上直接 `python -m backend.graph_service` 自检；
路由层只负责把异常转成契约错误码。

数据约定（见 文档/知识图谱与GNN方案.md §8.1）：
    serving/manifest.json   计数、维度、has_embeddings
    serving/nodes.json      [{id,type,label,props}]
    serving/adjacency.json  {node_id: [{type,dst}]}  双向展开
"""
from __future__ import annotations

import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "kg", "out", "serving")

EDGE_TYPES = {
    "NEXT", "IN_CHAPTER", "IN_DOC", "CITES", "MENTIONS_DOC",
    "MENTIONS_EXT", "CITES_DOC", "ISSUED_BY", "HAS_TAG",
}


class GraphDataError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code          # NOT_FOUND | UNAVAILABLE
        self.message = message
        super().__init__(f"[{code}] {message}")


class GraphData:
    def __init__(self, serving_dir: str | None = None):
        self.dir = serving_dir or os.environ.get("KG_SERVING_DIR") or DEFAULT_DIR
        self._lock = threading.Lock()
        self._loaded = False
        self.manifest: dict = {}
        self.nodes: dict[str, dict] = {}
        self.adj: dict[str, list[dict]] = {}

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                with open(os.path.join(self.dir, "manifest.json"), encoding="utf-8") as f:
                    self.manifest = json.load(f)
                with open(os.path.join(self.dir, "nodes.json"), encoding="utf-8") as f:
                    self.nodes = {n["id"]: n for n in json.load(f)}
                with open(os.path.join(self.dir, "adjacency.json"), encoding="utf-8") as f:
                    self.adj = json.load(f)
            except FileNotFoundError as exc:
                raise GraphDataError(
                    "UNAVAILABLE",
                    f"图谱产物缺失：{exc.filename}（先跑 python kg/build_graph.py && python kg/export_serving.py）",
                ) from exc
            except (json.JSONDecodeError, KeyError) as exc:
                raise GraphDataError("UNAVAILABLE", f"图谱产物损坏：{exc}") from exc
            self._loaded = True

    # ---------- 对外 ----------

    def stats(self) -> dict:
        self.load()
        return {
            "version": self.manifest.get("version", "kg-v1"),
            "generated_at": self.manifest.get("generated_at"),
            "node_counts": self.manifest.get("node_counts", {}),
            "edge_counts": self.manifest.get("edge_counts", {}),
            "dim": self.manifest.get("dim"),
            "has_embeddings": bool(self.manifest.get("has_embeddings")),
            "total_nodes": sum(self.manifest.get("node_counts", {}).values()),
            "total_edges": sum(self.manifest.get("edge_counts", {}).values()),
        }

    def subgraph(self, clause_id: str, hops: int = 2, limit: int = 200,
                 edge_types: list[str] | None = None) -> dict:
        """以条文为中心取 k 跳子图。同时服务 `/neighbors`（hops=1）与 `/subgraph`。"""
        self.load()
        focus = self._resolve(clause_id)
        allowed = set(edge_types) if edge_types else None
        if allowed:
            unknown = allowed - EDGE_TYPES
            if unknown:
                raise GraphDataError("INVALID", f"未知边类型：{sorted(unknown)}")

        visited: dict[str, int] = {focus: 0}
        frontier = [focus]
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()
        truncated = False

        for depth in range(1, hops + 1):
            if not frontier:
                break
            nxt: list[str] = []
            for node in frontier:
                for link in self.adj.get(node, []):
                    if allowed and link["type"] not in allowed:
                        continue
                    dst = link["dst"]
                    if dst not in self.nodes:
                        continue
                    key = (node, link["type"], dst)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({"src": node, "type": link["type"], "dst": dst})
                    if dst not in visited:
                        if len(visited) >= limit:
                            truncated = True
                            continue
                        visited[dst] = depth
                        nxt.append(dst)
            frontier = nxt

        nodes = []
        for nid, depth in visited.items():
            n = self.nodes[nid]
            props = {k: v for k, v in n.get("props", {}).items()
                     if k in ("doc_id", "clause_no", "clause_index", "chapter", "doc_type",
                              "domain", "status", "org", "size")}
            nodes.append({"id": nid, "type": n["type"], "label": n["label"],
                          "depth": depth, "props": props})
        return {
            "clause_id": clause_id,
            "focus": focus,
            "hops": hops,
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def _resolve(self, clause_id: str) -> str:
        raw = (clause_id or "").strip()
        if not raw:
            raise GraphDataError("INVALID", "clause_id 不能为空")
        candidates = [raw] if raw.startswith("C:") else [f"C:{raw}", raw]
        for cid in candidates:
            if cid in self.nodes:
                return cid
        raise GraphDataError("NOT_FOUND", f"条文 {clause_id} 不在图谱中")


_INSTANCE: GraphData | None = None


def get_graph() -> GraphData:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = GraphData()
    return _INSTANCE


if __name__ == "__main__":  # 自检：不需要 fastapi
    g = get_graph()
    print(json.dumps(g.stats(), ensure_ascii=False, indent=2))
    sample = "NFRA-016-A26"
    sub = g.subgraph(sample, hops=1, limit=20)
    print(f"\n{sample} 1-hop: nodes={sub['node_count']} edges={sub['edge_count']}")
    for n in sub["nodes"][:6]:
        print(" ", n["type"], n["label"][:40], "depth", n["depth"])
    for e in sub["edges"][:6]:
        print("  ", e["type"], e["src"], "->", e["dst"])
