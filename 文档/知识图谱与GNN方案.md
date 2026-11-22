# 知识图谱与 GNN 方案（HGT + 检索重排）

版本 v1 · 2026-11-21 · 对应《前后端补齐整改计划》之外的**新增模块 M8**

> 一句话：在现有 8156 条条文之上建一张**六类节点的分层异构图**，用 HGT 做链接预测，
> 再把节点表示接进检索重排；图与表示离线产出，API/前端只读 JSON + npz，**服务端不需要 torch**。

## 0. 先说清楚边界

| 项 | 结论 |
|---|---|
| 目标 | 接进现有 RAG（链接预测为主 + 检索重排为辅），不是独立玩具 |
| 基线现状 | 现有检索 **top-5 47/47 = 100%**，所以 GNN 的指标必须落在 **top-1 / MRR / 引用与多跳** 上，不能拿召回率说事 |
| 训练环境 | 本机有 RTX 5060 Laptop（8GB），但**没装 torch/PyG、也没网**；训练由有依赖的机器执行 |
| 服务依赖 | 只读 `kg/out/serving/`（JSON + npz），**不引入 torch 到 backend 镜像** |
| 引用规模 | 182 处显式引用，126 可定位到库内条文（去重后 108 条 CITES 边）—— 稀有边必须用 5 折报 mean±std |

## 1. 图 schema（六类节点，十类边）

```
L1 条文层    Clause ──NEXT──> Clause
             Clause ──IN_CHAPTER──> Chapter
             Clause ──CITES──> Clause
L2 法规层    Clause/Chapter ──IN_DOC──> Doc
             Doc ──CITES_DOC──> Doc
             Clause ──MENTIONS_DOC──> Doc
             Clause ──MENTIONS_EXT──> ExternalLaw
L3 机构层    Doc ──ISSUED_BY──> Agency
             Doc ──HAS_TAG──> TopicTag
```

实测规模（`kg/out/graph_stats.json`，2026-11-21 实跑）：

| 节点 | 数量 | | 边 | 数量 |
|---|---:|---|---|---:|
| Clause | 8,156 | | NEXT（双向） | 15,994 |
| Chapter | 858 | | IN_DOC | 9,014（条 8,156 + 章 858）|
| Doc | 159 | | IN_CHAPTER | 7,024 |
| Agency | 23 | | MENTIONS_EXT | 765 |
| TopicTag | 22 | | MENTIONS_DOC | 461 |
| ExternalLaw | 453 | | ISSUED_BY | 232 |
| **合计** | **9,671** | | HAS_TAG / CITES_DOC / CITES | 206 / 203 / 108 |
| | | | **合计** | **34,007** |

孤立点 **0**，平均度 **7.03**。

### 1.1 为什么不是"所有节点连一张图"

`IN_DOC` / `ISSUED_BY` / `HAS_TAG` 只允许从**下一层指向上层**，条文不能直接连机关；
`NEXT` 只在同文档内；`CITES` 只在条文间。这样每个节点的邻居语义是确定的，
可视化可以按层过滤，也给 HGT 的边类型提供了明确含义。

### 1.2 章节用"星型"而不是"章内全连接"

同章两两相连是 **51,038 条边**（仅一个 173 条的章就 14,878 对），会主导消息传递。
改成 `Chapter` 节点星型后降到 **7,024 条**，且"同章"仍是两跳可达。

## 2. 抽取与归一（这部分决定图的质量）

| 规则 | 处理 | 实测影响 |
|---|---|---|
| 章节名 | 去 PDF 点线+页码、全角空格、所有空白 | 7 处页脚噪声被清掉 |
| 法规简称 | 全称 / 去「中华人民共和国」/ 去尾部括号年份；**有歧义的别名丢弃** | 《证券法》等简称从"库外"回到库内 |
| 手工别名 | `基金法 → 证券投资基金法` | 13 条提及归位 |
| 泛称 | `条例/办法/规定/通知/…` 不建 ExternalLaw 节点 | 去掉 48 条伪边 |
| 库外法规 | 保留为 `ExternalLaw` 节点；`公司法 → 中华人民共和国公司法` 归一 | 453 个库外法规，公司法 110 次最高 |
| 引用 | 正则 `《X》第N条`；X 命中别名 → 若 N 也在该法规里则 CITES，否则 MENTIONS_DOC | 182 处：条文级 126、库外 49、泛称 7；去重后 CITES 108 |

**抽检**：`kg/out/spotcheck_sample.csv` 100 条（126 条条文级引用全进 + 其余随机补足），
人工填 `verdict`（对/错）与 `note`。验收标准：条文级引用精确率 ≥ 85%，否则回到别名表补规则。

## 3. 节点特征（不重训 embedding）

| 节点 | 特征 |
|---|---|
| Clause | 现有 bge-m3 向量（8156×1024，已归一化） |
| Chapter / Doc / ExternalLaw | 成员/提及条文向量均值 → L2 归一化 |
| Agency / Tag | 所属法规向量均值 → L2 归一化 |

实测 6 类节点 **零向量行 = 0**（`kg/out/features/features.json`）。

## 4. 数据划分（防泄漏）

- **按文档** 80/10/10：127 / 15 / 17 份。测试集的文档在训练中不出现。
- 消息传递图**只含 train 边**；val/test 边只用于评估。
- 稀有边（CITES 108 条）另给**按文档 5 折**（`split.json` 的 `by_fold`，14/8/35/25/26），
  报告 mean±std，不用单次划分的 12 条边下结论。
- 负例：val/test 固定采样（1:1 存盘）；训练在线采样。

## 5. 模型（HGT）

```
x_t → Linear_t(1024→256) → [HGTConv(256, heads=4) + LayerNorm + ReLU + 残差] × 2 → z_t
score(边类型 k) = (z_src ⊙ r_k) · z_dst          # 关系专属 DistMult
```

- 训练目标：每种边类型独立做二分类（正边 vs 随机负边），BCE，按类型平均。
- 优化：Adam 1e-3，weight decay 1e-5，batch=4096 正例/类型，早停看 val MRR。
- 导出：模型选择结束后，用**全部边**再编码一次作为最终节点表示（常见做法，需在论文里写明）。
- 消融：去边类型（只留 NEXT）、随机初始化特征、HGT→RGCN、1 层 vs 2 层。

## 6. 评估协议

| 任务 | 指标 | 数据 |
|---|---|---|
| 链接预测 | Hits@1 / Hits@10 / MRR / mean rank（100 负例） | 每个边三元组分别报；CITES 用 5 折 |
| 检索重排 | **top-1 / top-3 / MRR** + bootstrap 95% CI | 现有 50 题（47 可答） |
| 消融 | 上述指标 ± 组件 | 同数据 |

**不许做的事**：拿 top-5 命中率当主指标（基线已 100%）；拿单次划分的 CITES AUC 当结论。

## 7. 检索重排（GNN 怎么接回 RAG）

候选集取现有检索 top-20，每个候选算 4 个特征：

1. `base_score`：现有混合检索分数（bge + BM25 + rerank）
2. `gnn_cos`：GNN 节点向量与 bge query 向量的余弦
3. `graph_prox`：与 top-3 种子的 1/2 跳邻居关系（同章 / 引用 / 提及，按边类型加权）
4. `degree` + `status_effective`

在**验证集问题**上拟合一个线性/逻辑回归 ranker（不碰测试问题），再报测试集 top-1/MRR。
问题也要按文档划分，避免"问题引用的条文在训练图里"这种泄漏。

## 8. 服务化与前端

### 8.1 离线产物（服务端只读这些）

```
kg/out/serving/manifest.json     计数、维度、是否带 embedding
kg/out/serving/nodes.json        9,671 个节点（id/type/label/props）
kg/out/serving/adjacency.json    {node_id: [{type,dst}]} 双向展开
kg/out/serving/embeddings.npz    有训练产物时：每类节点 float16 表示
```

### 8.2 新增接口（契约 10 → 13，需同步 `契约/API.md` 并 P1 签字）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/graph/stats` | 节点/边计数、维度、版本 |
| GET | `/api/v1/graph/neighbors?clause_id=&hops=1&edge_types=CITES,NEXT` | 1~2 跳邻居 |
| GET | `/api/v1/graph/subgraph?clause_id=&hops=2&limit=200` | 前端子图（节点+边） |

错误码复用：`40001` 参数、`40401` 条文不存在、`50301` 图谱产物缺失。

### 8.3 前端 `/graph` 页

- 左侧 5 个页面加"图谱"入口；默认展示当前条文 2 跳子图，节点按层着色
- 边类型过滤（NEXT/IN_CHAPTER/CITES/…），点上节点跳 `/clause/:id`
- 子图上限 200 节点（9,671 全图画不动，也不该画）
- 不新增前端依赖：用 SVG 圆形布局/简易力导向，避免再装 d3

## 9. 交付物与排期（1~2 周）

| 天 | 交付 |
|---|---|
| D1–D2 | `kg/build_graph.py` + 抽检 CSV（**已在本机完成，100 条待填**） |
| D3–D4 | `kg/features.py` / `split.py`（**已完成**）+ `train_hgt.py` 首跑、链接预测指标 |
| D5 | `kg/export_serving.py` + `backend` 三个 graph 接口 |
| D6–D7 | 前端 `/graph` 页 + 子图接口联调 |
| D8–D10 | 重排实验、消融、5 折 CITES 评估 |
| D11–D12 | 文档、测试、报告图表 |

**当前进度**（2026-11-21）：
- ✅ 建图 / 特征 / 划分 / 导出：脚本与产物齐全，已在本机实跑（9,671 节点 / 34,007 边）；
- ✅ 3 个 `/graph/*` 接口 + 前端 `/graph` 页：代码完成，服务层自检通过；前端未 `npm build`；
- ⏳ `train_hgt.py`：代码已写，**未运行**（导出机器无 torch/PyG）；
- ⏳ 重排实验、CITES 5 折评估、100 条引用抽检：未开始。

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| PyG 版本/`HGTConv` metadata 不匹配 | 训练脚本首跑失败 | 用 PyG≥2.5；报错贴回；备选 RGCN（接口不变） |
| 引用边太少（108） | 链接预测指标不稳 | 5 折 mean±std；主指标用 MENTIONS_DOC/CITES_DOC 等更密的边 |
| 重排增益接近 0 | 无法证明价值 | 主指标 top-1/MRR；同时报"引用推理"案例，不硬吹提升 |
| ExternalLaw 噪声（453 个） | 图被泛称污染 | 已过滤泛称；抽检里单列库外法规，精确率低于阈值就收回 |
| 全图可视化卡顿 | 前端体验差 | 只画 ≤200 节点子图；全图只给统计 |
| 训练/评估泄漏 | 指标虚高 | 按文档划分；问题按文档划分；导出用全边需在论文说明 |
