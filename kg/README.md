# kg：知识图谱与 HGT

六类节点（Clause / Chapter / Doc / Agency / TopicTag / ExternalLaw）的分层异构图，
用 HGT 做链接预测，并把节点表示用于检索重排。完整设计见
[../文档/知识图谱与GNN方案.md](../文档/知识图谱与GNN方案.md)。

## 谁跑哪一步

| 步骤 | 需要什么 | 在哪跑 |
|---|---|---|
| `build_graph.py` | Python + openpyxl | 任何机器（**已在本机实跑**） |
| `features.py` | + numpy | 任何机器（**已实跑**） |
| `split.py` | + numpy | 任何机器（**已实跑**） |
| `train_hgt.py` | + torch + PyG（建议 GPU） | 有 GPU 的机器 |
| `export_serving.py` | Python + numpy | 任何机器 |
| API / 前端 | 只要 `kg/out/serving/` | 服务端 |

## 四步

```bash
# ① 建图（约 10 秒）
python kg/build_graph.py
#    nodes.jsonl / edges.jsonl / graph_stats.json / citation_edges.csv / spotcheck_sample.csv

# ② 节点特征（约 20 秒，吃 产出/embeddings 的 32MB）
python kg/features.py
#    features/{Clause,Chapter,Doc,Agency,TopicTag,ExternalLaw}.npy + node_index.json

# ③ 划分（按文档 80/10/10 + 5 折）
python kg/split.py
#    split/edges.npz + split.json

# ④ 训练（GPU 机器）
pip install -r requirements-kg.txt
python kg/train_hgt.py --epochs 200 --device cuda
#    model_hgt.pt / embeddings.npz / train_metrics.json

# ⑤ 导出给服务端（不需要 torch）
python kg/export_serving.py
#    serving/nodes.json / adjacency.json / embeddings.npz / manifest.json
```

## 本机已经产出的（可直接检查）

```
kg/out/graph_stats.json      9,671 节点 / 34,007 边 / 0 孤立点
kg/out/citation_edges.csv    182 处引用的逐条解析（126 条可定位到条文）
kg/out/spotcheck_sample.csv  100 条人工抽检样本（verdict / note 待填）
kg/out/features/            6 类节点 × 1024 维（38MB）
kg/out/split/               10 个边三元组的 train/val/test + 5 折
kg/out/serving/             nodes/adjacency/manifest（5.9MB，无 embedding）
```

## 注意

- `train_hgt.py` **还没有在真机跑过**（导出机器没有 torch/PyG）。PyG 的版本兼容问题
  通常出在 `HGTConv` 的 `metadata` 参数与 `ToUndirected()` 生成的 `rev_*` 边类型上，
  报错请把 traceback 贴回来。
- `kg/out/features/*.npy` 与 `serving/adjacency.json` 合计约 44MB，**不建议入 Git**，
  重跑 ①~③ 即可再生成（前提是 `产出/embeddings` 在）。
- 引用边只有 108 条，测试集 12 条：**不要用单次划分的 AUC 下结论**，
  稀有边用 `split.json` 里的 5 折做 mean±std（`train_hgt.py` 目前只跑单次划分，
  5 折评估脚本是下一步）。
