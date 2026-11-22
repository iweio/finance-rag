"""HGT 异构图编码器 + 关系型链接预测头（PyG）。

- `HGTLinkPredictor.encode(x_dict, edge_index_dict)` 返回每类节点的表示（hidden 维）
- `score(key, src_idx, dst_idx)` 用**关系专属的 DistMult 向量**打分，兼容 10 种边

只依赖 torch + torch_geometric；**服务端不加载这个文件**（用 kg/out/serving/ 的导出产物）。
"""
from __future__ import annotations

import torch
import torch.nn as nn
from torch_geometric.nn import HGTConv, Linear as PyGLinear


class HGTLinkPredictor(nn.Module):
    def __init__(self, metadata, in_dims: dict[str, int], hidden: int = 256,
                 heads: int = 4, layers: int = 2):
        super().__init__()
        self.node_types, self.edge_types = metadata
        self.edge_keys = ["|".join(et) for et in self.edge_types]
        self.proj = nn.ModuleDict({
            t: PyGLinear(in_dims[t], hidden) for t in self.node_types
        })
        self.convs = nn.ModuleList([
            HGTConv(hidden, hidden, metadata, heads=heads) for _ in range(layers)
        ])
        self.norms = nn.ModuleDict({t: nn.LayerNorm(hidden) for t in self.node_types})
        # 每个关系一个向量：score = (z_src * r) · z_dst
        self.rel = nn.ParameterDict({
            k: nn.Parameter(torch.randn(hidden) * 0.02) for k in self.edge_keys
        })

    def encode(self, x_dict, edge_index_dict):
        base = {t: self.proj[t](x_dict[t]) for t in self.node_types}
        h = base
        for conv in self.convs:
            h = conv(h, edge_index_dict)
            # 残差：训练图只含 train 边时，val/test 文档的节点可能是孤立点，
            # 纯 HGT 输出会是 0 向量；加回投影后的自身特征，保证仍有表示。
            h = {t: self.norms[t](h[t] + base[t]).relu() for t in self.node_types}
        return h

    def score(self, h, key: str, src_idx: torch.Tensor, dst_idx: torch.Tensor) -> torch.Tensor:
        src_type, _, dst_type = key.split("|")
        r = self.rel[key]
        return ((h[src_type][src_idx] * r) * h[dst_type][dst_idx]).sum(dim=-1)
