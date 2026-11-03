# DEPLOY 契约

容器化部署。来源《分工》模块 M5 与 `5-对外API契约`。主责 **P1**，P3 配合。

命题硬性要求：**Docker 容器化部署**。目标是「换一台机器，`docker compose up` 就能起来」。

## 1. 部署拓扑

```
                    ┌──────────────┐
   浏览器 ──────────▶│    nginx     │ :80
                    └──────┬───────┘
                    /      │      \  /api
              ┌────▼───┐   │   ┌───▼──────┐
              │frontend│   │   │ backend  │ :8000  FastAPI
              │ 静态文件│   │   │ (P3 API) │
              └────────┘   │   └───┬──────┘
                           │       │  import
                           │   ┌───▼──────┐
                           │   │ rag_core │        P1 的核心，不是独立服务
                           │   └───┬──────┘
                           │       │
              ┌────────────▼──┐ ┌──▼────────────┐
              │    mysql      │ │    milvus     │ :19530
              │    :3306      │ └──┬─────┬──────┘
              └───────────────┘    │     │
                            ┌──────▼┐ ┌──▼────┐
                            │ etcd  │ │ MinIO │
                            └───────┘ └───────┘
```

**`rag_core` 不是独立服务**，是被 `backend` 直接 import 的 Python 包（[MODULE.md](MODULE.md)）。这样没有进程间序列化开销，对 ≤2 秒响应指标有利。

**etcd + MinIO 是 Milvus 的依赖**，不是可选项。Milvus standalone 必须挂这两个。

## 2. 服务清单

| 服务 | 镜像 | 端口 | 作用 | 主责 |
|---|---|---|---|---|
| `nginx` | `nginx:1.25-alpine` | 80:80 | 反代 + 前端静态文件 | P1 |
| `frontend` | 自建（多阶段 → nginx） | — | Vue3 构建产物 | P3 |
| `backend` | 自建（`python:3.11-slim`） | 8000:8000 | FastAPI + `rag_core` | P3 / P1 |
| `mysql` | `mysql:8.0` | 3306:3306 | 业务库（[DB.md](DB.md) §2） | P3 |
| `milvus` | `milvusdb/milvus:v2.5.27` | 19530:19530, 9091:9091 | 向量库（[DB.md](DB.md) §3） | P2 |
| `etcd` | `quay.io/coreos/etcd:v3.5.x` | 内部 | Milvus 元数据 | P1 |
| `minio` | `bitnamilegacy/minio:2025.7.23-debian-12-r5` | 9000:9000, 9001:9001 | Milvus 对象存储 | P1 |

**版本要写死到小版本**（`v2.5.27` 而不是 `v2.5.x`）。Milvus 的 schema 与索引参数跨小版本有差异，用 `latest` 会出现「昨天还能跑，今天 `create_index` 报参数错」。

### 2.1 MinIO 镜像取不到了（2025-10 实测）

`minio/minio` 的 Docker Hub 仓库**已返回 404** —— 不是镜像站被墙，是 MinIO 自己撤下了镜像：

```
$ curl -o /dev/null -w "%{http_code}" https://hub.docker.com/v2/repositories/minio/minio/
404
```

后果：**官方那份 `milvus-standalone-docker-compose.yml` 现在对谁都跑不起来**，它引用的 `minio/minio:RELEASE.2023-03-20T20-16-18Z` 已经不存在。逐个试过的来源：

| 来源 | 结果 |
|---|---|
| `minio/minio`（Docker Hub） | **404，仓库没了** |
| `quay.io/minio/minio` | 401 UNAUTHORIZED |
| `registry.min.io/minio/minio` | pull access denied |
| daocloud / nju / 1ms / 1panel / dockerproxy 等镜像站 | 403 或 not found |
| **`bitnamilegacy/minio`** | **可用** ✅ |

**当前方案**：`bitnamilegacy/minio:2025.7.23-debian-12-r5`。Bitnami 2025 年 8 月把旧镜像整体搬到了这个 `legacy` 仓库。MinIO 对 Milvus 只是「S3 兼容对象存储」，换发行方不影响功能。

**两处与官方版不同，改配置时别照抄官方 compose：**

| | 官方 `minio/minio` | Bitnami `bitnamilegacy/minio` |
|---|---|---|
| 账号密码变量 | `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| 数据目录 | `/data` | `/bitnami/minio/data` |

**★ 这是临时方案**：`legacy` 仓库 Bitnami 明确说是过渡性质的，会下架。届时三条退路，按优先级：

1. 换别的 S3 兼容实现（SeaweedFS / Garage），Milvus 侧只改 `MINIO_ADDRESS`
2. 自建 MinIO 镜像：基础镜像 + `minio` 二进制
3. Milvus 侧改 `common.storageType: local` 完全不用对象存储 —— **未验证**。`milvus.yaml` 里这句的注释写的是「please adjust in **embedded** Milvus: local」（2.4.15 与 2.5.27 原文一字不差），standalone 下能否工作没试过，别当已知可行方案

**P2 开发用的最小 compose 是 `docker-compose.milvus.yml`**（仓库根目录，3 个容器：etcd + minio + milvus）。文件名不是 `docker-compose.yml`，所以不会被 `docker compose up` 自动捡到，必须显式指定：

```bash
docker compose -f docker-compose.milvus.yml up -d
```

最终 7 服务的 `docker-compose.yml`（§7 交付物 #3）由 P1/P3 出，把这里的三个服务照搬过去即可。

### 2.2 Milvus 与 pymilvus 必须同为 2.5 线（**改了 P1 定的版本号**）

技术方案写的是 `v2.4.x`，**实际停不下来**：`pymilvus 2.4.x` 的 `FieldSchema` 把 `nullable`
**静默吞掉** —— 建表不报错，但字段实际不可为空，行式列式两条插入路径都拒 `None`。
而 [DB.md](DB.md) §3.1.2 有 7 个字段必须能存空值（`expire_date` 159/159 全空、
`chapter` 1132/8156、`source_url` 18/159）。完整实测对照见 DB.md §3.1.2。

```
milvusdb/milvus:v2.5.27      # 服务端
pymilvus>=2.5,<2.6           # 客户端。一对，别只升一边
```

**⚠ 这一条动的是 P1 的交付物，需要 P1 知情。** `backend` 的环境里也要装
`pymilvus>=2.5,<2.6`。**没测过 2.4 客户端连 2.5 服务端**，别混用。

## 3. 环境变量（`.env`）

`.env` **不入 Git**，仓库里放 `.env.example`。`docker-compose.yml` 用 `${VAR}` 引用。

```bash
# ---- MySQL ----
MYSQL_ROOT_PASSWORD=rag_root_pwd
MYSQL_DATABASE=rag_finance
MYSQL_USER=rag
MYSQL_PASSWORD=rag_pwd

# ---- Milvus ----
MILVUS_HOST=milvus
MILVUS_PORT=19530
MILVUS_COLLECTION=finance_clause

# ---- 后端 ----
DATABASE_URL=mysql+pymysql://rag:rag_pwd@mysql:3306/rag_finance?charset=utf8mb4
API_PREFIX=/api/v1

# ---- embedding（与 DB.md / MODULE.md 联动）----
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024
QUERY_PREFIX=为这个句子生成表示以用于检索相关文章：

# ---- LLM ----
LLM_PROVIDER=dashscope
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-xxxxxxxx

# ---- 数据 ----
DATA_DIR=./数据源
```

### 3.1 两个必须写进 `.env` 而不是写死在代码里的值

**`QUERY_PREFIX`**：bge 系列的查询前缀，只在 query 侧加，passage 侧不加（[MODULE.md](MODULE.md) §7）。写死在两处代码里迟早会有一处漂移，漂移的后果是检索掉点且不报错。放 `.env`，两边都读同一个变量。

**`EMBED_MODEL` + `EMBED_DIM`**：第 4 周冻结后不改（[README.md](README.md) §3）。中途换 = 8156 行全部重新编码 + 重建索引 + P1 查询侧同步改。

## 4. 挂载卷

`产出` 的目录约定（P2 的 `pipeline.py` 产出，[MODULE.md](MODULE.md) §8）：

| 路径 | 内容 | 谁写 | 谁读 |
|---|---|---|---|
| `产出/语料元数据.xlsx` | 159×19 元数据表 | `_meta.py` + 手工追加（见 [README.md](README.md) §4 #5） | `/api/v1/meta/filters`、`pipeline.py` 自己 |
| `产出/clean/{doc_id}.txt` | 清洗后全文（9 条规则后） | `pipeline.py` | 人工核查、切分的输入 |
| `产出/chunks/{doc_id}.jsonl` | 一行一个条文 chunk | `pipeline.py` | 向量化（第 7 步） |
| `产出/embeddings/` | 向量文件（`parts/part-*.npy` + 同名 `.jsonl` + `manifest.json`） | 向量化 | 入库（第 8 步） |
| `产出/抽检报告.md` | 20 条条文与原始文件的逐条对照 | 人工抽检 | 第 10 周「知识库搭建完成」的证据 |
| `产出/语料时效核查报告.html` | 159 份的发布/生效/废止日期核查 | `_meta.py` 之后人工 | 差异化要求②「时效性」的证据 |
| `产出/topic_tags_词表.md` | 22 词业务条线词表 + 159 份逐份回标 + 命中分布 | 第 6 步人工回标 | [SPEC.md](SPEC.md) §8.4「标签归属」那条争议的证据 |
| `产出/org_归一表.md` | `org` 简称 → 全称的映射，与逐行改动前后 | `脚本/_org_norm.py` | [README.md](README.md) §4 #7 的留痕；P1 的 `meta/filters` 依赖归一后的值 |
| `产出/评估集/50题.jsonl` | 第 9 步的 50 题评测集（41 `fact` + 6 `filter` + 3 `abstain`） | P2 | `脚本/_eval_retrieval.py`、第 10 步的生成评测 |
| `产出/评估集/结果_top{k}.json` | 每题的期望条文、实际 top-k、命中位次，**外加本次检索的全部参数**（`ef` / 模型 / `QUERY_PREFIX` / 默认筛选表达式） | `_eval_retrieval.py` | 第 10 周「检索准确率」的证据；**改语料后重跑会覆盖** |

`ef` 取多少不是拍脑袋定的：`python 脚本/_ef_sweep.py` 一次扫完 `ef ∈ {0,8,16,32,64,128,256,512}`，
只改这一个变量、其余全冻结，报出**哪一档开始不再变**（饱和点）。**只在它打印时看，不写文件** ——
要留档的是上面那份 `结果_top{k}.json`。语料规模一变饱和点就要重测，结论与复测表见 [MODULE.md](MODULE.md) §8.1。
| `产出/日志/` | 各步骤的运行日志（切分双数、向量化耗时、依赖版本） | 各脚本重定向 | 验收取证，**不是**给人日常读的 |

**临时产物（哪天要清，先看这段）。** 第 5 步补 `source_url` 时留下三类中间文件，都在 `产出/`：

| 文件 | 是什么 | 处置 |
|---|---|---|
| `产出/_source_url_todo.json` | 138 份待查清单 | **保留到第 4 周** |
| `产出/_url_results_*.json` | 6 个并行子代理各自的查证结果（含 `evidence` 字段） | **保留到第 4 周** |
| `产出/_pubdate_results.json` | 日期查证代理对 `PBC-016/069`、`SSE-001/005`、`SZSE-002` 五份的逐条结论（含**官方页原文引用**与 `url`）。README §4 #9 里那 2 处「只能靠外部官方页」的依据就存在这里 | **保留到第 4 周** |
| `产出/_bak_语料元数据_*.xlsx` | 写表前的整表备份 | 确认入库无误后即可删 |

前两类是**查证过程的原始记录**：`source_url` 的每一行都能顺着它回溯到是哪次查证、
依据是什么、代理当时标了什么置信度。第 4 周 P1/P3 若对某个 URL 有疑问，靠它回答，
**不是**靠 `note` 里那半句话。三类都带 `_` 前缀，**不入 Git**。

`产出/embeddings/parts/*.npy` 共约 32 MB，**不入 Git**（`.gitignore` 里那条有争议，理由写在文件注释里）。只留 `manifest.json`。要重建：`python 脚本/embed.py`，**全量约 27 分钟**（实测见 `产出/日志/第7步-向量化-全量8156条.log`）。

> 日志文件名里带着当次的行数（`第4步-切分全量158份.log`、`第7步-向量化-全量7949条.log`、
> `第7步-向量化-全量8156条.log`），那是**每次真实跑过的记录，不改名、不覆盖** ——
> 它们是「当时的库确实是那个规模」的取证。语料扩充后新跑一次就多一份新日志，旧的那份留着。
> **所以日志里出现 `7949` 不是漏改的数字**，别去把它改成 `8156`——那会把取证改成假证。

```yaml
volumes:
  mysql_data:      # MySQL 数据
  milvus_data:     # Milvus 数据
  etcd_data:       # etcd 数据
  minio_data:      # MinIO 对象
  ./数据源:/data:ro            # 语料只读，供 P2 的 pipeline 用
  ./产出:/output:ro            # 给 rag_core / backend **只读**
```

**只读的边界是「谁写谁挂 rw」。** `rag_core` / `backend` 一律 `:ro`；负责落产物的那个一次性任务（`pipeline.py` / 向量化 / 入库）单独挂 **`:rw`**：

```yaml
  ./产出:/output               # ingest 任务：要落 clean / chunks / embeddings
```

**`数据源` 一律只读（`ro`）**：容器里的解析流程不该改动原始语料。中文路径在 Linux 容器内是 UTF-8，无问题；但**在 Windows 宿主机上跑 `docker compose` 时，路径要在 `docker-compose.yml` 里写相对路径**，写绝对中文路径会因编码在部分 Docker Desktop 版本下挂载失败。所以这里挂的是目录 `./产出`，不是 `./产出/语料元数据.xlsx` 这种中文文件名。

`产出/语料元数据.xlsx` 挂进去是给 `/api/v1/meta/filters` 用的：筛选字典的 `count` 可以直接从这张表算（[API.md](API.md) §5），不必查 Milvus。**第 4 周定：是读 xlsx 还是查 Milvus**，二者 `count` 口径必须一致。

## 5. 启动与自检

```bash
docker compose up -d
docker compose ps                    # 7 个服务都应 Up / healthy
curl http://localhost/api/v1/health
```

`/api/v1/health` 必须返回 `{"milvus": {"status": "ok", "row_count": 8156}}`（[API.md](API.md) §8）。

### 5.1 空库不报错是本项目最大的隐性风险

技术方案 §3.1 已经点明：对扫描件直接 `get_text()` 会得到空字符串或乱码，**程序不会报错**，你会以为解析成功，直到第 10 周发现知识库是空的。

容器化之后这个风险会放大——`docker compose up` 起来一切正常，`health` 返回 `ok`，但 Milvus 里 0 条。所以：

| 关卡 | 检查 | 通过标准 |
|---|---|---|
| 启动后 | `health.data.milvus.row_count` | **= 8156**（不是 > 0） |
| 启动后 | `docker compose ps` 的 mysql / milvus | `healthy` 而非仅 `Up` |
| 每次入库后 | `python 脚本/_verify.py` | 15 项全 PASS |

`row_count` 精确比对 8156 而不是 `> 0`：8156 是 `pipeline.py --all` 切出的行数，上游锚点数 8036 由 `_verify.py` 15 项校验过。出现 `8100` 或 `8200` 都说明有一批文件读失败或重复入库了。

两个数是一对：**锚点 8036（`_verify.py`）+ 超长条二次切 120 = 入库 8156（`pipeline.py`）**。只对一个不算过。

**这两个数会随语料扩充而变**，别把它当成恒定值背下来。改完语料跑一次 `python 脚本/_counts.py`，它把该改的数一次报全（锚点数 / 二次切数 / 各字段空值数），照着抄进本文与 [SPEC.md](SPEC.md) §6、[API.md](API.md) §8、[DB.md](DB.md) §3.3。

### 5.2 健康检查的 `depends_on`

```yaml
backend:
  depends_on:
    mysql:  { condition: service_healthy }
    milvus: { condition: service_healthy }
```

用 `condition: service_healthy` 而不是裸 `depends_on`。裸写只等容器启动，MySQL 首次初始化要几十秒，backend 会在 MySQL 就绪前连上去然后崩掉，表现为「第一次 `up` 起不来，再 `up` 一次就好了」。

## 6. 跨环境复现

命题要求「跨环境复现」。以下三项是常见的复现失败点：

| 项 | 处理 |
|---|---|
| 中文文件名 | 宿主机 Git Bash 传中文路径会乱码；compose 里一律用**相对路径**，不用 `${PWD}` 拼绝对路径 |
| 时区 | `TZ=Asia/Shanghai`，MySQL 与 backend 都要设。不设则 `created_at` 差 8 小时 |
| 依赖版本 | `requirements.txt` / `package-lock.json` 全锁小版本；`python:3.11-slim` 不用 `python:3` |

## 7. 交付物（第 16 周）

| # | 交付物 | 验收 |
|---|---|---|
| 1 | `Dockerfile`（backend） | 多阶段或 slim 基础镜像 |
| 2 | `Dockerfile`（frontend） | 构建后由 nginx 托管 |
| 3 | `docker-compose.yml` | 7 服务，`depends_on: service_healthy` |
| 4 | `.env.example` | 不含真实密钥 |
| 5 | `nginx.conf` | `/` → frontend，`/api` → backend:8000 |
| 6 | `一键启动脚本` | Linux `start.sh` + Windows `start.bat` |
| 7 | `README` 部署章节 | 陌生机器上照做能起来 |

第 6 项要**同时给两份**：组里开发机是 Windows（Trae + Git Bash），演示机大概率是 Linux。