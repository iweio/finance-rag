"""把现有 bge-m3 向量挂到六类节点上，产出训练用的特征矩阵。

关键取舍：**不重新训练 embedding**。
- Clause：直接用 `产出/embeddings/parts/*.npy`（8156×1024，已归一化）
- Chapter / Doc / ExternalLaw：由所属/提及的条文向量求均值再归一化
- Agency / Tag：由所属法规向量求均值再归一化
- 没有邻居的节点给零向量（训练时由 HGT 的投影层兜底）

输出（默认 kg/out/features/）：
    node_index.json   {类型: [节点ID, ...]}  顺序 = 特征矩阵行序
    Clause.npy 等       float32，行数 = node_index[类型] 长度
    features.json     维度、各类型行数、零向量节点数
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kg import schema as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kg", "out")
EMB_DIR = os.path.join(ROOT, "产出", "embeddings")
FEAT_DIR = os.path.join(OUT, "features")


def load_clause_vectors() -> tuple[dict[str, np.ndarray], int]:
    """按分片顺序读回 (clause_id -> 1024 维向量)。"""
    manifest = json.load(open(os.path.join(EMB_DIR, "manifest.json"), encoding="utf-8"))
    dim = int(manifest["dim"])
    vecs: dict[str, np.ndarray] = {}
    for i in range(int(manifest["shards"])):
        npy = os.path.join(EMB_DIR, "parts", f"part-{i:03d}.npy")
        ids = os.path.join(EMB_DIR, "parts", f"part-{i:03d}.jsonl")
        arr = np.load(npy)
        with open(ids, encoding="utf-8") as f:
            id_list = [json.loads(line)["clause_id"] for line in f if line.strip()]
        if arr.shape[0] != len(id_list) or arr.shape[1] != dim:
            raise SystemExit(f"分片 {i} 形状对不上：{arr.shape} vs {len(id_list)}×{dim}")
        for cid, vec in zip(id_list, arr):
            vecs[cid] = vec.astype(np.float32)
    if len(vecs) != int(manifest["total"]):
        raise SystemExit(f"向量数 {len(vecs)} != manifest.total {manifest['total']}")
    return vecs, dim


def _normalize(mat: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return (mat / norm).astype(np.float32)


def build(out_dir: str = OUT) -> dict:
    vecs, dim = load_clause_vectors()
    nodes = [json.loads(l) for l in open(os.path.join(out_dir, "nodes.jsonl"), encoding="utf-8")]
    edges = [json.loads(l) for l in open(os.path.join(out_dir, "edges.jsonl"), encoding="utf-8")]

    index: dict[str, list[str]] = {t: [] for t in S.NODE_TYPES}
    for n in nodes:
        index[n["type"]].append(n["id"])
    for t in index:
        index[t].sort()
    pos = {t: {nid: i for i, nid in enumerate(ids)} for t, ids in index.items()}

    # ---- Clause：直接取向量 ----
    clause_mat = np.zeros((len(index[S.CLAUSE]), dim), dtype=np.float32)
    for nid, row in pos[S.CLAUSE].items():
        cid = nid[2:]
        if cid not in vecs:
            raise SystemExit(f"chunk {cid} 没有向量")
        clause_mat[row] = vecs[cid]

    # ---- 由 Clause 聚合：Chapter / Doc / ExternalLaw ----
    def aggregate(dst_type: str, edge_type: str) -> np.ndarray:
        rows = len(index[dst_type])
        acc = np.zeros((rows, dim), dtype=np.float32)
        cnt = np.zeros(rows, dtype=np.int32)
        for e in edges:
            if e["type"] != edge_type:
                continue
            src_row = pos[S.CLAUSE].get(e["src"])
            dst_row = pos[dst_type].get(e["dst"])
            if src_row is None or dst_row is None:
                continue
            acc[dst_row] += clause_mat[src_row]
            cnt[dst_row] += 1
        acc[cnt > 0] /= cnt[cnt > 0, None]
        return _normalize(acc)

    chapter_mat = aggregate(S.CHAPTER, S.IN_CHAPTER)
    doc_from_clause = aggregate(S.DOC, S.IN_DOC)
    ext_mat = aggregate(S.EXTERNAL, S.MENTIONS_EXT)

    # ---- 由 Doc 聚合：Agency / Tag ----
    def aggregate_from_doc(dst_type: str, edge_type: str) -> np.ndarray:
        rows = len(index[dst_type])
        acc = np.zeros((rows, dim), dtype=np.float32)
        cnt = np.zeros(rows, dtype=np.int32)
        for e in edges:
            if e["type"] != edge_type:
                continue
            src_row = pos[S.DOC].get(e["src"])
            dst_row = pos[dst_type].get(e["dst"])
            if src_row is None or dst_row is None:
                continue
            acc[dst_row] += doc_from_clause[src_row]
            cnt[dst_row] += 1
        acc[cnt > 0] /= cnt[cnt > 0, None]
        return _normalize(acc)

    agency_mat = aggregate_from_doc(S.AGENCY, S.ISSUED_BY)
    tag_mat = aggregate_from_doc(S.TAG, S.HAS_TAG)

    mat_by_type = {
        S.CLAUSE: clause_mat, S.CHAPTER: chapter_mat, S.DOC: doc_from_clause,
        S.AGENCY: agency_mat, S.TAG: tag_mat, S.EXTERNAL: ext_mat,
    }

    os.makedirs(FEAT_DIR, exist_ok=True)
    with open(os.path.join(FEAT_DIR, "node_index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    report = {"dim": dim, "types": {}}
    for t, mat in mat_by_type.items():
        np.save(os.path.join(FEAT_DIR, f"{t}.npy"), mat)
        zero = int((np.linalg.norm(mat, axis=1) == 0).sum())
        report["types"][t] = {"rows": int(mat.shape[0]), "zero_rows": zero,
                              "file": f"features/{t}.npy"}
    with open(os.path.join(FEAT_DIR, "features.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    rep = build()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n已写入 {FEAT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
