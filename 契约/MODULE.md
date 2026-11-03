# MODULE 契约：RagEngine

P1 → P3 的模块边界。来源《分工》sheet `6-模块间接口边界`，冻结时间 **第 4 周**。

**P3 只依赖下面 4 个方法，不感知检索内部实现**（不 import Milvus、不拼 embedding、不知道有没有 rerank）。这样 P3 可以用 mock 实现并行开工，不必等 P1 把链路打通。

## 1. 位置与签名

```python
# rag_core/service.py
class RagEngine:
    def answer_stream(self, question: str, filters: Filters | None,
                      session_id: str) -> Iterator[Event]: ...
    def search(self, keyword: str, filters: Filters | None,
               page: int, size: int) -> SearchResult: ...
    def get_clause(self, clause_id: str) -> ClauseDetail: ...
    def get_related_clauses(self, clause_id: str, top_k: int) -> list[ClauseBrief]: ...
```

四个方法**一一对应** [API.md](API.md) 的前四个接口，P3 的活是把它们包成 HTTP：

| `RagEngine` 方法 | 对应接口 |
|---|---|
| `answer_stream` | `POST /api/v1/chat`（[API.md](API.md) §1） |
| `search` | `POST /api/v1/search`（§2） |
| `get_clause` | `GET /api/v1/clause/{clause_id}`（§3） |
| `get_related_clauses` | `GET /api/v1/clause/{clause_id}/related`（§4） |

后四个接口（`meta/filters` / `history` / `favorite` / `health`）不走 `RagEngine`，由 P3 直接查 MySQL 与 Milvus 连接（`health` 的 `row_count` 见 [DB.md](DB.md) §5）。

## 2. `Filters`

```python
@dataclass
class Filters:
    domain: str | None = None    # 银行|证券|保险|外汇|综合
    status: str | None = None    # 有效|尚未生效|已废止
    org:    str | None = None
```

三个字段**全部可空**，`None` 表示不限。P1 负责把它拼成 Milvus 的 `filter=` 表达式（[DB.md](DB.md) §4）：

```python
conds = []
if filters.status: conds.append(f'status == "{filters.status}"')
if filters.domain: conds.append(f'domain == "{filters.domain}"')
if filters.org:    conds.append(f'org == "{filters.org}"')
expr = " and ".join(conds) if conds else None
```

**注意**：`filters.status` 为 `None` 时不加条件，**不要**默认补 `status == "有效"`。用户主动想查已废止条文时要查得到。

## 3. 数据模型

四个返回模型与 [API.md](API.md) 的字段名**逐字对应**。P1 定这些类，P3 直接 `asdict()` 后塞进响应包，不做字段改名。

```python
@dataclass
class ClauseBrief:                  # API.md §1.1 sources[] 与 §4 related[]
    clause_id:      str
    doc_id:         str
    doc_title:      str
    clause_no:      str             # 第五十三条
    clause_index:   int             # 53
    chapter:        str | None
    snippet:        str             # 条文正文，过长截断
    score:          float           # 0..1
    status:         str
    effective_date: int | None      # YYYYMMDD
    source_url:     str | None

@dataclass
class RelatedClause(ClauseBrief):   # API.md §4，多一个字段
    relation_type:  str             # 同条|同章|引用|相似

@dataclass
class ClauseDetail:                 # API.md §3，完整元数据卡片
    clause_id:      str
    doc_id:         str
    doc_title:      str
    clause_no:      str
    clause_index:   int
    chapter:        str | None
    text:           str             # 不截断
    doc_type:       str
    domain:         str
    org:            str
    doc_number:     str | None
    publish_date:   int | None
    effective_date: int | None
    expire_date:    int | None
    status:         str
    source_url:     str | None
    fetched_at:     int

@dataclass
class SearchResult:                 # API.md §2
    total:  int                     # 命中总数，不是本页条数
    page:   int
    size:   int
    items:  list[SearchItem]

@dataclass
class SearchItem:                   # 比 ClauseBrief 少 4 个、多 1 个
    clause_id:  str
    doc_title:  str
    clause_no:  str
    snippet:    str
    highlight:  str                 # 命中词 <em> 包裹；P1 必须做 HTML 转义
    status:     str
    source_url: str | None
    score:      float
```

`ClauseDetail` 的 17 个字段名 = [SPEC.md](SPEC.md) §2 的列名（`doc_number` / `publish_date` / `effective_date` / `expire_date` / `status` / `source_url` …）+ `clause_id`/`clause_no`/`clause_index`/`chapter`/`text`。**没有一处自造的名字。**

## 4. `answer_stream` 的事件流

返回 `Iterator[Event]`，P3 逐个写成 SSE。事件类型与 [API.md](API.md) §1 的四类一致：

```python
@dataclass
class Event:
    event: str          # delta | sources | score | done
    data:  dict
```

| `event` | `data` | 说明 |
|---|---|---|
| `delta` | `{"text": str}` | 增量文本 |
| `sources` | `{"sources": [ClauseBrief...]}` | 溯源条文 |
| `score` | `{"score": float}` | 可信度 0..1 |
| `done` | `{"qa_id": int, "elapsed_ms": int}` | 结束；`qa_id` 由 P3 落库后回填 |

**顺序不可变**：`delta*` → `sources` → `score` → `done`。前端按此假设渲染（收到 `sources` 就渲染卡片，不等 `done`）。

`qa_id` 的产生有跨模块依赖：QA 记录要落 MySQL（P3 的活），但 `done` 事件由 P1 发。约定 **P1 发 `done` 时 `qa_id` 暂填 `-1`，P3 收到后落库、改写成真值再转发给前端**。或者由 P3 在转发前先落库、把 `qa_id` 注入 `done` 的 `data` —— 两种都可，**第 4 周定死一种**。

## 5. 异常：`RagError`

```python
class RagError(Exception):
    """RAG 核心的唯一对外异常。"""
    def __init__(self, code: str, message: str): ...
```

P1 内部**所有**异常（Milvus 连接失败、LLM 超时、模型加载失败）都必须包成 `RagError` 抛出，**不得**让 `pymilvus` / `langchain` / `openai` 的原生异常穿过边界——否则 P3 的 `except` 写不准，且堆栈会漏到前端。

P3 侧转换（[API.md](API.md) §0.4）：

| `RagError.code` | HTTP | API `code` |
|---|---|---|
| `RETRIEVE_FAILED` | 500 | 50001 |
| `GENERATE_FAILED` | 500 | 50002 |
| `NOT_FOUND` | 404 | 40401 |
| 其它 | 500 | 50001 |

**`get_clause` 查不到条文时抛 `RagError("NOT_FOUND", ...)`，不要返回 `None`** —— 返回值在类型标注上不可空，用 `None` 会让 P3 忘判空。

## 6. P3 的 mock 契约

P3 第 5-6 周不等 P1，先按本文档写 mock：

```python
class MockRagEngine:
    def get_clause(self, clause_id: str) -> ClauseDetail:
        # 逐字段抄 NFRA-016 的真值（`产出/语料元数据.xlsx` 序号 159 + `产出/chunks/NFRA-016.jsonl`）
        return ClauseDetail(clause_id="NFRA-016-A26", doc_title="商业银行资本管理办法",
                            clause_no="第二十六条", clause_index=26,
                            chapter="第二章 资本监管指标计算和监管要求 第三节 资本监管要求",
                            text="第二十六条 商业银行各级资本充足率不得低于如下最低要求：\n"
                                 "(一)核心一级资本充足率不得低于5%。\n"
                                 "(二)一级资本充足率不得低于6%。\n"
                                 "(三)资本充足率不得低于8%。",
                            doc_type="部门规章", domain="银行", org="国家金融监督管理总局",
                            doc_number="国家金融监督管理总局令2023年第4号",
                            publish_date=20231026, effective_date=20240101, expire_date=None,
                            status="有效",
                            source_url="https://www.gov.cn/zhengce/202311/content_6913410.htm",
                            fetched_at=20261027)
```

mock 的值直接抄 P2 交付的 159 行真数据，**不要自造**「某某银行法」这类假标题——假数据会在联调时被当成真数据漏进截图和文档。

> **本段原先那个例子就是反面教材，2026-10-27 换掉。** 它写的 `clause_id="PBC-001-A53"`
> 配 `doc_title="商业银行资本管理办法"` —— **两个字段指向两份不同的文件**（`PBC-001` 是
> 《中华人民共和国商业银行法》的 `doc_id`）；`doc_number="…令（2023年第4号）"` 与
> `publish_date=20231101` 也都不是真值（真实公布日是 `20231026`）。当时库里还没有这份文件，
> 是照「大概长这样」编的。**这正是本段下面那句警告说的情形**：编出来的值会被截图带进论文。

## 7. 交付 Checklist（P1 → P3，第 6 周）

对应 技术方案 §10 的 8 项，P3 视角：

| # | 交付物 | 易漏 |
|---|---|---|
| 1 | `RagEngine` 4 方法可用（真实现或 mock） | — |
| 2 | 本文档确认（签名、异常、事件顺序） | — |
| 3 | embedding 模型名 + 版本 + **QUERY_PREFIX 字符串**（见 §8） | ★ 极易漏 |
| 4 | `filters` → Milvus `filter=` 的写法（[DB.md](DB.md) §4） | — |
| 5 | 确认 `status` / `effective_date` / `domain` 可过滤 | — |
| 6 | 筛选项枚举清单 + `count` 统计口径（[API.md](API.md) §5） | 易漏 |
| 7 | `qa_id` 回填方案二选一（§4） | 易漏 |
| 8 | 排错手册：`RagError.code` 与成因对照 | — |

第 3 项漏掉的后果在 技术方案 §10 写得很直白：**检索会莫名掉点，且排查时看不出任何报错**，只会觉得「怎么召回的都不对」。

## 8. embedding 参数（第 4 周冻结）

`RagEngine` 内部编码 query 时用的参数。P3 不直接用，但要**在 `.env` 里配同一份**（[DEPLOY.md](DEPLOY.md) §3.1），否则 P1 与部署环境会漂移。

```python
EMBED_MODEL  = "BAAI/bge-m3"
EMBED_DIM    = 1024
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："   # ← 只加在 query 侧
```

```python
query_vec = model.encode(QUERY_PREFIX + question, normalize_embeddings=True)
```

三条不可违反：

1. **前缀只加在 query 侧，passage 侧不加。** 加错任何一侧都掉点，且不报错。
2. **`normalize_embeddings=True`**，与 Milvus 的 `metric_type="COSINE"` 配对（[DB.md](DB.md) §3.3）。
3. **模型与维度冻结后不改**（[README.md](README.md) §3）。改一次 = 8156 行全部重新编码 + 重建索引 + P1 查询侧同步改。

## 8.1 `search_params` 显式带 `ef`（**P1 必读**）

```python
search_params = {"metric_type": "COSINE", "params": {"ef": 256}}
```

**传，不是因为「不传会掉点」—— 这条旧结论在 8156 条规模下没复现。** 见下。

### 复测（2026-10-27，8156 条重建索引后）

`python 脚本/_ef_sweep.py`，同一批查询向量、同一个索引，**只改 `ef` 一个变量**：

| `ef` | top-5 | top-1 | MRR |
|---|---|---|---|
| 不传（服务端默认） | 47 / 47 | 39 / 47 | 0.895 |
| 8 | 46 / 47 | 38 / 47 | 0.874 |
| **16** | **47 / 47** | **39 / 47** | **0.895** |
| **256** | **47 / 47** | **39 / 47** | **0.895** |
| 512 | 47 / 47 | 39 / 47 | 0.895 |

`ef ≥ 16` 起就一条不再变，**饱和点 16**（上一轮 7949 条时量到的是 64，本轮更低 ——
饱和点并没有随库变大而上移，反而低了，所以**不能把它当成随规模单调增长的量**）。
契约仍取 **256**：留余量，且在 8156 条规模下这点开销测不出来。

### ★ 旧结论作废，理由说清楚

上一轮记的是「不传 `ef` 只有 97.8%（45/46），传了才 100%」。**本轮不传就是 47/47，
和不传之外的所有档位一样。** 两种可能，这里分不出是哪种：

- 服务端默认 `ef` 本来就 ≥ 16，上一轮那次 45/46 是**建图随机性**撞上的；
- 或者上一轮那次默认值确实更低（换过镜像/配置）。

**分不出就照实写，不要挑一个自己喜欢的说法留下来。** 但有一件事是真的：
**差 1 条题（46 vs 47）落在这套索引的重建噪声里** —— HNSW 建图带随机性，同一批向量
跑两次 `ingest.py --recreate`，图不同，排位边界上的那一条会翻面。本轮 `ef=8` 掉的
那 1 条也是同一个量级，**不能据此说「`ef=8` 不够」**，只能说「`ef=8` 时这条翻了」。

### 所以为什么还是必须写死 `ef`

**理由是「可复现」，不是「更准」。** 不传时用的是服务端默认，那个值不由本项目控制：
换镜像、换 `milvus` 版本、换部署参数，它都可能变，而**变了不会报错，只会让分悄悄不同**。
评测分要能跨人、跨次、跨机器比较，参数就得写在调用里。同理，`_eval_retrieval.py`
每次跑都把 `ef` 连同模型名、`QUERY_PREFIX`、默认筛选表达式一起写进 `结果_top{k}.json` ——
**没有这几项的分，事后没法判断是不是同一口径下量出来的。**

要演示「不传」的行为：`python 脚本/_eval_retrieval.py --ef 0`。