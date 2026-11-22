# 在「有 Docker 的机器」上跑起来：交接清单

> 目标：**换一台装了 Docker 的机器，一条命令起来**。
> 这份清单按"第一次跑会遇到的顺序"排，不是按模块排。**先照 §1 准备，再照 §2 跑，卡住直接查 §4。**

## 0. 这套路径已经在真机上跑通过一次（2026-11-22）

本机 Docker 29.2.1 + compose v5.0.2，7 个服务全 healthy、13 个接口逐个验过、
`vite build` 与 `pytest`（38 项）都过了。证据见 §5。

**但这一台是"脏机器"**：宿主机 80 被 IIS/http.sys 占、3306 被本机 MySQL 占，
所以本机靠 **`docker-compose.override.yml`** 把 nginx 挪到 8080、MySQL 挪到 3307。
这份 override **不入 Git**（里面是 `${DEEPSEEK_API_KEY:?}` 这种只有本机才有的值，
干净机器上 `up` 会被它自己拦掉）。你的机器若端口干净，照 §2 原样跑就行；
若也被占，**别改 `docker-compose.yml`**，照抄一份 override 出来改。

换一台**干净**的机器（演示机/评委机）仍然是第一次跑，§4 列的是已知最可能卡的那几个。

## 1. 准备（10 分钟）

| 项 | 要求 | 检查 |
|---|---|---|
| Docker | Docker Desktop（Windows 需 WSL2 后端）或 Docker Engine + compose v2 | `docker compose version` |
| 内存 | ≥ 8GB 可用 | Milvus standalone + MySQL + backend(torch) 一起吃内存 |
| 磁盘 | ≥ 15GB 空闲 | 镜像 + 模型（约 4.5GB）+ 向量库 |
| 端口 | **80 / 3306 / 19530 / 9091 / 8000** 空闲 | Windows 上 80 常被 IIS、3306 常被本机 MySQL 占 |
| 网络 | 首次构建要联网（pip + npm + 镜像） | 国内建议配好镜像加速 |

**拷贝时别漏这几个目录/文件**（缺了后面一定失败）：

```
产出/embeddings/parts/*.npy      # 32MB，向量；.gitignore 默认不提交，git clone 的仓库里没有
产出/chunks/*.jsonl              # 159 个文件，BM25 索引要用
产出/语料元数据.xlsx              # /meta/filters 的文档级字典要用
kg/out/serving/*.json            # 3 个 /graph/* 接口只读这一份（6MB，入 Git）
frontend/src/api/mock/fixtures.json
脚本/                             # ingest / seed 要在容器里跑
```

`数据源/`（195 份原始文件、几百 MB）**不是必须的**，只有重跑 `pipeline.py` 才需要。

## 2. 五步跑起来（首次 20~40 分钟）

```bash
cd RAG/RAG

# ① 配置
cp .env.example .env
#    必填：LLM_API_KEY
#    必改：JWT_SECRET（随便一串 32 位以上随机字符）
#    国内建议：HF_ENDPOINT=https://hf-mirror.com

# ② 先验一遍 compose 语法（比直接 up 快，报错也清楚）
docker compose config > /dev/null && echo "compose OK"

# ③ 构建（第一次要装 torch/npm 依赖，慢）
docker compose build

# ④ 起 7 个服务
docker compose up -d
docker compose ps            # 等 7 个都 healthy（backend 有 120s start_period）

# ⑤ 灌库（**第一次必须做**，否则前端能开、问不出东西）
docker compose exec backend python 脚本/ingest.py --recreate
#    期望最后一行：落库 count(*) = 8156
```

验收：

```bash
curl -s http://localhost/api/v1/health
# data.mysql.status=ok、data.mysql.schema=ok、data.milvus.row_count=8156、data.mode=real

curl -s http://localhost/api/v1/meta/filters | head -c 300
# 能看到 5 个轴 + date_range

curl -s http://localhost/api/v1/graph/stats
# total_nodes=9671 total_edges=34007；has_embeddings=false 是正常的（没训 HGT）

# 浏览器
#   http://localhost          前端（登录 demo 账号需自己注册，或先匿名问答）
#   http://localhost:8000/docs 接口文档
```

上面用 `http://localhost`（80）。本机这类脏机器要加 `:8080`，自检也可以一条命令跑完 13 个接口：

```bash
python 脚本/_fullstack_check.py --base http://localhost:8080   # 45 项，全 PASS 退出码 0
```

> `import 产出/embeddings` 缺失时，第 ⑤ 步会报"向量文件不存在"。两条路：
> ① 把 `产出/embeddings/parts/*.npy` 一起拷过来（推荐，32MB）；
> ② 在容器里先 `docker compose exec backend python 脚本/embed.py`（要下模型，约 27 分钟）。

## 3. 只想先看界面（不灌库、不连 Milvus）

给朋友演示 UI 的最快路径：**单独构建前端镜像、用 Mock 模式跑**，只要 1~2 分钟。

```bash
docker build --build-arg VITE_USE_MOCK=true -t rag-frontend-mock ./frontend
docker run --rm -p 8080:80 rag-frontend-mock
# 打开 http://localhost:8080
```

Mock 自带 40 条检索结果、7 条条文详情、4 种关联关系；登录用任意新用户名 + 6 位以上密码注册即可
（mock 的 `demo/demo123` 也直接能登）。**这条路径不需要 MySQL/Milvus/后端。**

## 4. 最可能卡住的地方（按概率排序）

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | `docker compose config` 报 YAML 错 | 这份 compose **没用解析器校验过** | 把报错行号贴出来，按缩进改；多半是 `environment` 里中文/冒号没引号 |
| 2 | `bind: address already in use`（80 或 3306） | IIS / 本机 MySQL 占用 | 改 `docker-compose.yml` 的 `ports`，如 nginx `"8080:80"`、mysql `"3307:3306"`；改用 8080 访问 |
| 3 | 前端镜像 `npm install` 报 ERESOLVE | Vite 6 与 unplugin-* peer 范围窄 | Dockerfile 已加 `--legacy-peer-deps`；仍失败就把 `unplugin-auto-import`→`^19`、`unplugin-vue-components`→`^28`，重删 `package-lock.json` 再 build |
| 4 | backend 镜像构建 20 分钟以上 / 拉 torch 超时 | torch 走 PyPI 默认源、国内慢 | Dockerfile 已固定 CPU 版 index；还慢就把那行换成国内 pip 源（注释里写了） |
| 5 | 起来了但第一句问答卡 30 秒后失败 | 首次要下 bge-m3 + reranker（≈4.5GB），hf.co 连不上 | `.env` 设 `HF_ENDPOINT=https://hf-mirror.com`，然后 `docker compose restart backend`；或预先把权重放进 `model_cache` 卷并设 `TRANSFORMERS_OFFLINE=1` |
| 6 | `health` 的 `row_count=0` | 第 ⑤ 步没跑 / 跑失败 | `docker compose exec backend python 脚本/ingest.py --recreate`，看它报什么 |
| 7 | `docker compose ps` 里 backend 一直 `starting` | 首次 import torch 慢，或 MySQL 没 healthy | 看 `docker compose logs backend --tail=100`；MySQL 首次初始化要 30~60s |
| 8 | nginx healthy 但页面 502 | frontend 没 healthy 或没 build 成功 | `docker compose logs frontend`；`docker compose ps frontend` |
| 9 | 前端能开、接口 404 | 根 nginx 的 `/api` 反代没生效 | 确认访问的是 `http://localhost/api/v1/health` 而不是 `/api/health`（少一段 v1 就是 404） |
| 10 | `COPY 产出/chunks` 报找不到 | 中文路径 + 老版本 Docker 的编码问题 | 升级 Docker Desktop；或用 BuildKit（`DOCKER_BUILDKIT=1 docker compose build`） |
| 11 | backend 一直 unhealthy，日志刷 `Milvus Proxy is not ready yet` | **depends_on 的 healthcheck 只保证 TCP 通**，Milvus Proxy 注册要几秒；backend 在这一瞬间连上就把坏连接缓存了 | `docker restart rag-backend` 即好。别去改 Milvus |
| 12 | `/graph/*` 返回 `50301 图谱产物缺失` | `kg/out/serving/` 没拷过来，或 compose 没挂上 | `docker compose exec backend ls /app/kg/out/serving`；缺了就重跑 `python kg/build_graph.py && python kg/export_serving.py` |
| 13 | `curl http://localhost/api/v1/health` 返回 IIS 的 404 页 | 80 被 Windows 的 http.sys/IIS 接走，请求根本没到 nginx | 本机走 override 的 8080；`netstat -ano \| findstr :80` 看占用 |

## 5. 验证到什么程度（2026-11-22 本机实测）

| 项 | 状态 |
|---|---|
| `docker compose config` / `build` | ✅ 两个镜像都重建成功（backend 带 `kg/`、frontend 带图谱页） |
| `docker compose up -d` 7 服务 healthy | ✅ 全 healthy；backend 有过一次 §4-#11 的 Milvus 竞态，restart 后恢复 |
| `ingest.py --recreate` 在容器里跑 | ✅ 早先跑过，`/health` 的 `row_count=8156` 一直在 |
| 前端 `vite build` | ✅ 在镜像里构建通过（含 `GraphView.vue`，队友那版从未构建过） |
| `pytest` | ✅ 38 项全过（宿主机跑；backend 镜像里没装 pytest，它是交付镜像不是 CI 镜像） |
| 13 个接口逐个走 nginx | ✅ `脚本/_fullstack_check.py` 45 项全 PASS，含 `/chat` 真 LLM 的 SSE 事件顺序、`/graph` 三接口的 40401/40001 错误包 |
| 检索基线没动 | ✅ 合并图谱后重跑 `脚本/_eval_retrieval.py`：top-5 47/47=100%、top-1 83.0%、MRR 0.895，与 `产出/评估集/结果_top5.json` 逐项相同 |
| 契约/数据一致性 | 🟡 `_contract_check.py` 只在合并前跑过；图谱不新增 Milvus 字段，预期不变 |

**还没验证的两件事**：`kg/train_hgt.py`（本机 anaconda 的 torch 是 `2.14.1+cpu`，没装
torch-geometric，Blackwell 卡还要另装 CUDA 版 torch —— 见 文档/知识图谱与GNN方案.md §0），
以及图谱**接进检索重排**（队友交付的 `/graph/*` 是只读可视化，检索链路一行没改）。
