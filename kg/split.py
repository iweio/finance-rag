"""按**文档**做链接预测的划分，并为验证/测试采样固定负例。

为什么按文档而不是按边随机划分：
条文之间大量边是同文档相邻（NEXT）和同章（IN_CHAPTER）。按边随机划分时，同一条边的
两端会在训练集里同时出现，模型只要记住"同文档就相连"就能拿高分 —— 这叫**泄漏**，
测出来的 AUC 不可信。按文档划分后，测试集里的文档在训练中完全没出现过。

**按三元组存边**：`IN_DOC` 同时存在 Clause→Doc 与 Chapter→Doc 两种源，
如果只按边名存，两种源的整数下标会串在一起（实测会被误去重 41 条）。
所以 key 一律是 `{源类型}|{边类型}|{目标类型}`。

输出（kg/out/split/）：
    edges.npz      每个三元组：{key}_src / _dst / _split / _fold / _neg_src / _neg_dst
    split.json     文档划分、各三元组各 split/折的边数、负例数

split：0=train, 1=val, 2=test。负例只给 val/test（训练时在线采样，避免文件过大）。
"""
from __future__ import annotations

import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kg import schema as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kg", "out")
SPLIT_DIR = os.path.join(OUT, "split")
SEED = 42
N_FOLDS = 5


def load() -> tuple[list[dict], list[dict], dict]:
    nodes = [json.loads(l) for l in open(os.path.join(OUT, "nodes.jsonl"), encoding="utf-8")]
    edges = [json.loads(l) for l in open(os.path.join(OUT, "edges.jsonl"), encoding="utf-8")]
    index = json.load(open(os.path.join(OUT, "features", "node_index.json"), encoding="utf-8"))
    return nodes, edges, index


def main() -> int:
    nodes, edges, index = load()
    pos = {t: {nid: i for i, nid in enumerate(ids)} for t, ids in index.items()}
    node_doc = {}
    for n in nodes:
        if n["type"] in (S.DOC, S.CLAUSE, S.CHAPTER):
            node_doc[n["id"]] = n["props"].get("doc_id")

    docs = sorted({d for d in node_doc.values() if d})
    rng = random.Random(SEED)
    rng.shuffle(docs)
    n_train = int(len(docs) * 0.8)
    n_val = int(len(docs) * 0.1)
    doc_split = {d: (0 if i < n_train else 1 if i < n_train + n_val else 2)
                 for i, d in enumerate(docs)}
    # 5 折（按文档分组）：稀有边（CITES 仅 108 条）用它报 mean±std，避免"测试集 12 条边"的 AUC
    fold_docs = docs[:]
    random.Random(SEED).shuffle(fold_docs)
    doc_fold = {d: i % N_FOLDS for i, d in enumerate(fold_docs)}

    def split_of(nid: str) -> int:
        return doc_split.get(node_doc.get(nid, ""), 0)

    def fold_of(nid: str) -> int:
        return doc_fold.get(node_doc.get(nid, ""), 0)

    # ---- 按三元组收集：(src_idx, dst_idx) -> (split, fold) ----
    grouped: dict[str, dict[tuple[int, int], tuple[int, int]]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for e in edges:
        stype = S.split_id(e["src"])[0]
        dtype = S.split_id(e["dst"])[0]
        si = pos[stype].get(e["src"])
        di = pos[dtype].get(e["dst"])
        if si is None or di is None:
            continue
        key = f"{stype}|{e['type']}|{dtype}"
        grouped.setdefault(key, {})[(si, di)] = (split_of(e["src"]), fold_of(e["src"]))
        meta[key] = (stype, dtype)

    rng = random.Random(SEED + 1)
    arrays: dict[str, np.ndarray] = {}
    report = {
        "seed": SEED,
        "docs": {"train": n_train, "val": n_val, "test": len(docs) - n_train - n_val,
                 "total": len(docs)},
        "edge_types": {},
    }

    for key, mapping in grouped.items():
        stype, dtype = meta[key]
        items = list(mapping.items())
        pairs = [(si, di, sp) for (si, di), (sp, _fd) in items]
        folds = [fd for _, (_sp, fd) in items]
        positives = set(mapping)
        dst_n = len(index[dtype])
        negs: list[tuple[int, int, int]] = []
        for si, di, sp in pairs:
            if sp == 0:
                continue
            for _ in range(50):
                cand = rng.randrange(dst_n)
                if cand != di and (si, cand) not in positives:
                    negs.append((si, cand, sp))
                    break

        arrays[f"{key}_src"] = np.asarray([p[0] for p in pairs], dtype=np.int64)
        arrays[f"{key}_dst"] = np.asarray([p[1] for p in pairs], dtype=np.int64)
        arrays[f"{key}_split"] = np.asarray([p[2] for p in pairs], dtype=np.int8)
        arrays[f"{key}_fold"] = np.asarray(folds, dtype=np.int8)
        if negs:
            arrays[f"{key}_neg_src"] = np.asarray([p[0] for p in negs], dtype=np.int64)
            arrays[f"{key}_neg_dst"] = np.asarray([p[1] for p in negs], dtype=np.int64)

        report["edge_types"][key] = {
            "src_type": stype, "dst_type": dtype,
            "total": len(pairs),
            "by_split": {int(s): sum(1 for p in pairs if p[2] == s) for s in (0, 1, 2)},
            "by_fold": {f: sum(1 for x in folds if x == f) for f in range(N_FOLDS)},
            "negatives": len(negs),
        }

    os.makedirs(SPLIT_DIR, exist_ok=True)
    np.savez_compressed(os.path.join(SPLIT_DIR, "edges.npz"), **arrays)
    with open(os.path.join(SPLIT_DIR, "split.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n已写入 {SPLIT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
