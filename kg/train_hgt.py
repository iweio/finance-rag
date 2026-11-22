"""训练 HGT 做链接预测，并导出节点表示。

前置（在有 torch/PyG 的机器上）：
    pip install -r requirements-kg.txt
    python kg/build_graph.py && python kg/features.py && python kg/split.py

训练（示例）：
    python kg/train_hgt.py --epochs 200 --hidden 256 --heads 4 --device cuda

产物：
    kg/out/model_hgt.pt          模型权重 + 配置
    kg/out/embeddings.npz        每类节点的表示（float32），键是节点类型
    kg/out/train_metrics.json    每轮 val/test 的 Hits@1/Hits@10/MRR/AUC

⚠ 本文件**没有在导出机器上运行过**（无 torch/PyG）。第一次跑若报 PyG 版本相关错误，
先确认 torch 与 PyG 版本匹配（PyG 2.5+ / torch 2.3+），再把报错贴回来。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F
from torch_geometric.data import HeteroData
from torch_geometric.transforms import ToUndirected

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kg.model_hgt import HGTLinkPredictor  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kg", "out")
FEAT = os.path.join(OUT, "features")
SPLIT = os.path.join(OUT, "split")


def load_index() -> dict[str, list[str]]:
    return json.load(open(os.path.join(FEAT, "node_index.json"), encoding="utf-8"))


def load_features(index: dict[str, list[str]]) -> dict[str, torch.Tensor]:
    return {t: torch.from_numpy(np.load(os.path.join(FEAT, f"{t}.npy"))) for t in index}


def build_data(index, feats, arrays, split: int | None) -> HeteroData:
    """split=None 用全部边（导出用）；否则只用 split==0 的边做消息传递。"""
    data = HeteroData()
    for t, ids in index.items():
        data[t].num_nodes = len(ids)
        data[t].x = feats[t]
    for key in arrays:
        if not key.endswith("_src"):
            continue
        et = key[:-4]
        src_type, rel, dst_type = et.split("|")
        src, dst = arrays[key], arrays[f"{et}_dst"]
        if split is not None:
            mask = arrays[f"{et}_split"] == split
            src, dst = src[mask], dst[mask]
        if len(src) == 0:
            continue
        data[(src_type, rel, dst_type)].edge_index = torch.tensor(
            np.stack([src, dst]), dtype=torch.long)
    return ToUndirected()(data)


def edge_set(arrays, et: str) -> set[tuple[int, int]]:
    src, dst = arrays[f"{et}_src"], arrays[f"{et}_dst"]
    return set(zip(src.tolist(), dst.tolist()))


def sample_batch(arrays, et: str, n_pos: int, rng, dst_n: int):
    src_all, dst_all = arrays[f"{et}_src"], arrays[f"{et}_dst"]
    pos_idx = rng.integers(0, len(src_all), size=min(n_pos, len(src_all)))
    pos_s, pos_d = src_all[pos_idx], dst_all[pos_idx]
    neg_d = rng.integers(0, dst_n, size=len(pos_s))
    src = torch.tensor(np.concatenate([pos_s, pos_s]), dtype=torch.long)
    dst = torch.tensor(np.concatenate([pos_d, neg_d]), dtype=torch.long)
    y = torch.cat([torch.ones(len(pos_s)), torch.zeros(len(neg_d))])
    return src, dst, y


@torch.no_grad()
def evaluate(model, h, arrays, et: str, split_val: int, dst_n: int,
             n_neg: int = 100, seed: int = 0) -> dict:
    src_all, dst_all = arrays[f"{et}_src"], arrays[f"{et}_dst"]
    mask = arrays[f"{et}_split"] == split_val
    src, dst = src_all[mask], dst_all[mask]
    if len(src) == 0:
        return {"n": 0}
    src_type = et.split("|")[0]
    dev = h[src_type].device
    known = edge_set(arrays, et)
    rng = np.random.default_rng(seed)
    ranks = []
    for s, d in zip(src.tolist(), dst.tolist()):
        cands = {int(d)}
        while len(cands) < n_neg + 1:
            c = int(rng.integers(0, dst_n))
            if c != d and (s, c) not in known:
                cands.add(c)
        cand = torch.tensor(sorted(cands), dtype=torch.long, device=dev)
        sc = model.score(h, et, torch.full_like(cand, s), cand).cpu().numpy()
        order = np.argsort(-sc)
        ranks.append(int(np.where(cand[order].cpu().numpy() == d)[0][0]) + 1)
    ranks = np.asarray(ranks)
    return {
        "n": int(len(ranks)),
        "hits@1": float((ranks <= 1).mean()),
        "hits@10": float((ranks <= 10).mean()),
        "mrr": float((1.0 / ranks).mean()),
        "mean_rank": float(ranks.mean()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--heads", type=int, default=4)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    index = load_index()
    feats = load_features(index)
    arrays = dict(np.load(os.path.join(SPLIT, "edges.npz")))
    split_report = json.load(open(os.path.join(SPLIT, "split.json"), encoding="utf-8"))
    keys = [k for k in split_report["edge_types"] if f"{k}_src" in arrays]

    data = build_data(index, feats, arrays, split=0)
    metadata = data.metadata()
    in_dims = {t: feats[t].shape[1] for t in index}
    model = HGTLinkPredictor(metadata, in_dims, hidden=args.hidden,
                             heads=args.heads, layers=args.layers).to(args.device)
    data = data.to(args.device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)
    rng = np.random.default_rng(args.seed)

    best, best_state, bad_epochs = -1.0, None, 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        h = model.encode(data.x_dict, data.edge_index_dict)
        loss = torch.zeros((), device=args.device)
        n_parts = 0
        for et in keys:
            if split_report["edge_types"][et]["by_split"]["0"] == 0:
                continue
            _, _, dst_type = et.split("|")
            src, dst, y = sample_batch(arrays, et, args.batch, rng, len(index[dst_type]))
            logits = model.score(h, et, src.to(args.device), dst.to(args.device))
            loss = loss + F.binary_cross_entropy_with_logits(logits, y.to(args.device))
            n_parts += 1
        loss = loss / max(n_parts, 1)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            h_eval = model.encode(data.x_dict, data.edge_index_dict)
        metrics = {}
        for et in keys:
            _, _, dst_type = et.split("|")
            metrics[et] = evaluate(model, h_eval, arrays, et, split_val=1,
                                   dst_n=len(index[dst_type]))
        common = [m["mrr"] for m in metrics.values() if m.get("n", 0) >= 5]
        val_mrr = float(np.mean(common)) if common else 0.0
        history.append({"epoch": epoch, "loss": float(loss), "val_mrr": val_mrr,
                        "metrics": metrics})
        if epoch % 10 == 0 or epoch == 1:
            print(f"[{epoch:3d}] loss={float(loss):.4f} val_mrr={val_mrr:.4f}")
        if val_mrr > best:
            best, best_state, bad_epochs = val_mrr, {k: v.detach().cpu() for k, v in model.state_dict().items()}, 0
        else:
            bad_epochs += 1
            if bad_epochs >= args.patience:
                print(f"early stop at epoch {epoch}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save({"state_dict": model.state_dict(), "in_dims": in_dims,
                "hidden": args.hidden, "heads": args.heads, "layers": args.layers},
               os.path.join(OUT, "model_hgt.pt"))

    # 最终导出：用**全部边**再编码一遍（模型选择已经结束，这是常见做法）
    full = build_data(index, feats, arrays, split=None).to(args.device)
    model.eval()
    with torch.no_grad():
        z = model.encode(full.x_dict, full.edge_index_dict)
    np.savez_compressed(os.path.join(OUT, "embeddings.npz"),
                        **{t: z[t].detach().cpu().numpy().astype(np.float32) for t in index})

    final = {}
    for et in keys:
        _, _, dst_type = et.split("|")
        final[et] = {
            "val": evaluate(model, z, arrays, et, 1, len(index[dst_type])),
            "test": evaluate(model, z, arrays, et, 2, len(index[dst_type])),
        }
    report = {"args": vars(args), "best_val_mrr": best, "final": final, "history": history}
    with open(os.path.join(OUT, "train_metrics.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v["test"] for k, v in final.items()}, ensure_ascii=False, indent=2))
    print(f"\n已写入 {OUT}/model_hgt.pt 与 embeddings.npz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
