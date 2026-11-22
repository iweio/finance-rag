"""知识图谱与 GNN（HGT）模块。

对外只暴露三件事：
    kg.schema            节点/边类型常量与 ID 规则
    kg.build_graph       从 产出/ 构建六类节点图
    kg.features/split    特征与划分（训练前的数据准备）

训练与评估脚本（train_hgt.py / eval_retrieval.py）需要 `requirements-kg.txt` 里的
torch + torch_geometric，**不在服务运行时依赖里**；服务只读 `kg/out/serving/` 的导出产物。
"""
