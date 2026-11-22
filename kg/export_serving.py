"""把图与 GNN 表示导出成**服务端可直接用**的产物（不需要 torch/PyG）。

API 与前端只读 `kg/out/serving/`：
    nodes.json        节点（id/type/label/props 精简）
    adjacency.json    {node_id: [{type, dst, dir}]}，双向展开
    embeddings.npz    有训练产物时才有：每类节点 float16 表示
    manifest.json     版本、计数、维度、是否带 embedding

用法：
    python kg/export_serving.py
    python kg/export_serving.py --no-embeddings   # 只导出图（未训练时）
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kg", "out")
SERVING = os.path.join(OUT, "serving")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embeddings", action="store_true")
    args = ap.parse_args()

    nodes = [json.loads(l) for l in open(os.path.join(OUT, "nodes.jsonl"), encoding="utf-8")]
    edges = [json.loads(l) for l in open(os.path.join(OUT, "edges.jsonl"), encoding="utf-8")]
    os.makedirs(SERVING, exist_ok=True)

    keep = {"doc_id", "doc_title", "clause_no", "clause_index", "chapter", "size",
            "doc_type", "domain", "status", "org", "publish_date", "effective_date",
            "source_url", "clause_count", "text_len"}
    slim = []
    for n in nodes:
        slim.append({
            "id": n["id"], "type": n["type"], "label": n["label"],
            "props": {k: v for k, v in n["props"].items() if k in keep and v not in (None, "")},
        })
    with open(os.path.join(SERVING, "nodes.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(slim, f, ensure_ascii=False)

    adj: dict[str, list[dict]] = collections.defaultdict(list)
    for e in edges:
        adj[e["src"]].append({"type": e["type"], "dst": e["dst"]})
        adj[e["dst"]].append({"type": e["type"], "dst": e["src"]})
    with open(os.path.join(SERVING, "adjacency.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(adj, f, ensure_ascii=False)

    has_emb = False
    src_npz = os.path.join(OUT, "embeddings.npz")
    if not args.no_embeddings and os.path.isfile(src_npz):
        with np.load(src_npz) as z:
            np.savez_compressed(os.path.join(SERVING, "embeddings.npz"),
                                **{k: z[k].astype(np.float16) for k in z.files})
        has_emb = True

    index = json.load(open(os.path.join(OUT, "features", "node_index.json"), encoding="utf-8"))
    manifest = {
        "version": "kg-v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "node_counts": dict(collections.Counter(n["type"] for n in nodes)),
        "edge_counts": dict(collections.Counter(e["type"] for e in edges)),
        "dim": 1024,
        "has_embeddings": has_emb,
        "node_index": {t: len(ids) for t, ids in index.items()},
    }
    with open(os.path.join(SERVING, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n已写入 {SERVING}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
