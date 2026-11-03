# API 契约

对外 HTTP 接口。8 个接口，来源《RAG项目任务拆分与三人分工.xlsx》sheet `5-对外API契约`。

- **实现方**：P3（FastAPI）
- **核心能力提供方**：P1 提供 `RagEngine`（见 [MODULE.md](MODULE.md)），P2 提供数据（见 [SPEC.md](SPEC.md)）
- **联调时间**：`health` 第 5 周，`chat`/`search`/`meta/filters` 第 6 周，`clause` 第 7 周，`related`/`history` 第 8 周，`favorite` 第 9 周

**本文件里的字段名是冻结的。** 前端的 axios、Postman 集合、Milvus 的 `output_fields` 都按此写，改名即返工。

## 0. 通用约定

### 0.1 前缀与编码

```
Base URL        http://localhost:8000
前缀            /api/v1
编码            UTF-8
Content-Type    application/json（chat 的响应为 text/event-stream）
```

### 0.2 日期与枚举

日期字段一律是 **int `YYYYMMDD`**，不是字符串，与 Milvus 标量字段一致（[DB.md](DB.md) §3）。

| 枚举字段 | 取值 | 源头 |
|---|---|---|
| `domain` | `银行` \| `证券` \| `保险` \| `外汇` \| `综合` | [SPEC.md](SPEC.md) §2 第 8 列 |
| `status` | `有效` \| `尚未生效` \| `已废止` | [SPEC.md](SPEC.md) §2 第 9 列 |
| `org` | 发文机关全称 | [SPEC.md](SPEC.md) §2 第 5 列 |
| `doc_type` | `法律` \| `行政法规` \| `司法解释` \| `部门规章` \| `规范性文件` \| `自律规则` \| `行业标准` | [SPEC.md](SPEC.md) §2 第 7 列 |

`status` 的第 4 个值 `已修订` 是**保留值**：当前数据里没有，启用时机见 [README.md](README.md) §4。

### 0.3 统一响应包（除 `chat` 外）

```json
{ "code": 0, "message": "ok", "data": { ... } }
```

`code = 0` 表示成功；非 0 即失败，`message` 是人话，`data` 为 `null`。

### 0.4 统一错误码

| HTTP | `code` | `message` | 触发条件 |
|---|---|---|---|
| 400 | 40001 | 参数校验失败 | 必填缺失、枚举越界、类型不符 |
| 404 | 40401 | 条文不存在 | `clause_id` 在 Milvus 中查不到 |
| 404 | 40402 | 会话不存在 | `session_id` 未在 `session` 表登记 |
| 500 | 50001 | 检索失败 | Milvus 不可用或查询异常（由 `RagError` 转来） |
| 500 | 50002 | 生成失败 | LLM 调用失败 |
| 500 | 50003 | 数据库错误 | MySQL 异常 |
| 503 | 50301 | 依赖不可用 | `health` 探到 Milvus / MySQL 不通 |

`RagEngine` 抛的 `RagError` 由 P3 统一转成 `50001`/`50002`，**不得透传堆栈给前端**（[MODULE.md](MODULE.md) §5）。

## 1. POST /api/v1/chat — 问答（流式）

唯一不用统一响应包的接口，返回 SSE。**这是系统的主动线。**

### 请求

```json
{
  "question":   "商业银行贷款应当遵守哪些资产负债比例管理规定",
  "session_id": "sess_7f3a91c2",
  "filters":    { "domain": "银行", "status": "有效", "org": null }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question` | string | 是 | 用户问题，1..500 字 |
| `session_id` | string | 是 | 前端生成，用于挂历史记录 |
| `filters` | object | 否 | 缺省即不带过滤 |
| `filters.domain` | string | 否 | 枚举见 §0.2；`null` 表示不限 |
| `filters.status` | string | 否 | 同上 |
| `filters.org` | string | 否 | 同上 |

`filters` 透传给 `RagEngine.answer_stream(question, filters, session_id)`，最终变成 Milvus 的 `filter=` 表达式（[DB.md](DB.md) §4）。**Milvus 是先过滤后检索**，不能先取 top100 再在 Python 里筛。

### 响应：SSE 事件流

`Content-Type: text/event-stream`。四类事件，按下面的顺序发：

```
event: delta
data: {"text": "商业银行贷款，应当遵守资产负债比例管理的规定："}

event: delta
data: {"text": "资本充足率不得低于百分之八，流动性资产余额与流动性负债余额的比例不得低于百分之二十五。"}

event: sources
data: {"sources": [ { ...ClauseBrief... } ]}

event: score
data: {"score": 0.87}

event: done
data: {"qa_id": 10241, "elapsed_ms": 1360}
```

| 事件 | 出现次数 | `data` 字段 | 说明 |
|---|---|---|---|
| `delta` | 0..N | `text` | 增量文本片段，前端拼接后 markdown 渲染 |
| `sources` | 1 | `sources[]` | 溯源条文列表，结构见 §1.1 |
| `score` | 1 | `score` | 可信度 0..1，前端渲染徽章 |
| `done` | 1 | `qa_id`、`elapsed_ms` | `qa_id` 是 `qa_history` 主键，供前端收藏 |

顺序约定：`delta*` → `sources` → `score` → `done`。`sources` 必须**先于最后一个 `delta` 之后的 `done`**，这样前端能在流结束时同步渲染卡片。

无依据拒答场景（P1 差异化要求）：`delta` 段输出拒答话术，`sources` 为**空数组**，`score` 低于阈值。

### 1.1 `ClauseBrief` 结构

`sources[]` 与 `/clause/{id}/related` 共用此结构。**字段名与 SPEC 的列名逐字对齐。**

```json
{
  "clause_id":    "PBC-001-A53",
  "doc_id":       "PBC-001",
  "doc_title":    "商业银行资本管理办法",
  "clause_no":    "第五十三条",
  "clause_index": 53,
  "chapter":      "第三章 资本充足率",
  "snippet":      "商业银行核心一级资本充足率不得低于百分之五。",
  "score":        0.91,
  "status":       "有效",
  "effective_date": 20240101,
  "source_url":   "https://www.gov.cn/..."
}
```

| 字段 | 类型 | 可空 | 对应 SPEC 列 |
|---|---|---|---|
| `clause_id` | string | 否 | Milvus 主键 |
| `doc_id` | string | 否 | 第 2 列 |
| `doc_title` | string | 否 | 第 4 列 |
| `clause_no` | string | 否 | 原文锚点原文，如「第五十三条」 |
| `clause_index` | int | 否 | 排序用，`A53` → 53 |
| `chapter` | string | 是 | 章节路径，无则 `null` |
| `snippet` | string | 否 | 条文正文（`text` 字段），过长的截断 |
| `score` | float | 否 | 相似度 / rerank 分，0..1 |
| `status` | string | 否 | 第 9 列 |
| `effective_date` | int | 是 | 第 11 列 |
| `source_url` | string | 是 | 第 15 列 |

## 2. POST /api/v1/search — 关键词检索

### 请求

```json
{ "keyword": "资本充足率", "page": 1, "size": 10,
  "filters": { "domain": "银行", "status": "有效" } }
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `keyword` | string | 是 | — | 1..100 字 |
| `page` | int | 否 | 1 | 从 1 开始 |
| `size` | int | 否 | 10 | 1..50 |
| `filters` | object | 否 | — | 同 §1 |

对 `RagEngine.search(keyword, filters, page, size)`。走**混合检索**（向量 + BM25，RRF 融合），不是纯 LIKE。

### 响应

```json
{
  "code": 0, "message": "ok",
  "data": {
    "total": 37,
    "page": 1,
    "size": 10,
    "items": [
      { "clause_id": "PBC-001-A53", "doc_title": "商业银行资本管理办法",
        "clause_no": "第五十三条", "snippet": "…核心一级资本充足率不得低于百分之五…",
        "highlight": "…核心一级<em>资本充足率</em>不得低于…",
        "status": "有效", "source_url": "https://…", "score": 0.91 }
    ]
  }
}
```

`data` 字段：`total`(int，命中总数，用于分页) / `page` / `size` / `items[]`。

`items[]` 是本接口专用的**精简结构**，比 `ClauseBrief` 少 `doc_id`/`clause_index`/`chapter`/`effective_date`，多一个 `highlight`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | int | 命中总数。**是总数不是本页条数**，前端分页器依赖它 |
| `highlight` | string | 命中词用 `<em>` 包裹的片段；前端用 `v-html` 渲染，**P3 必须在服务端做 HTML 转义**，否则条文里的尖括号会注入 |

## 3. GET /api/v1/clause/{clause_id} — 条文详情

### 请求

路径参数 `clause_id`，如 `PBC-001-A53`。对 `RagEngine.get_clause(clause_id)`。

### 响应

```json
{
  "code": 0, "message": "ok",
  "data": {
    "clause_id": "NPC-006-A39",
    "doc_id": "NPC-006",
    "doc_title": "中华人民共和国商业银行法",
    "clause_no": "第三十九条",
    "clause_index": 39,
    "chapter": "第四章 贷款和其他业务的基本规则",
    "text": "第三十九条 商业银行贷款，应当遵守下列资产负债比例管理的规定：\n(一)资本充足率不得低于百分之八；\n(二)流动性资产余额与流动性负债余额的比例不得低于百分之二十五；\n(三)对同一借款人的贷款余额与商业银行资本余额的比例不得超过百分之十；\n(四)国务院银行业监督管理机构对资产负债比例管理的其他规定。\n本法施行前设立的商业银行，在本法施行后，其资产负债比例不符合前款规定的，应当在一定的期限内符合前款规定。具体办法由国务院规定。",
    "doc_type": "法律",
    "domain": "银行",
    "org": "全国人民代表大会及其常务委员会",
    "doc_number": null,
    "publish_date": 20150829,
    "effective_date": 20151001,
    "expire_date": null,
    "status": "有效",
    "source_url": null,
    "fetched_at": 20261019
  }
}
```

**上面这条是库里真实存在的一条**（`产出/chunks/NPC-006.jsonl`），可逐字段对照。示例别再随手编：原先这里举的是《商业银行资本管理办法》，**那份不在本库 158 件里**，照着它写联调用例会查不到东西。

`data` 是完整的**元数据卡片**，字段名与 SPEC §2 的列名一一对应（`doc_number` / `publish_date` / `effective_date` / `expire_date` / `status` / `source_url` 全在此）。前端溯源卡片直接用这组字段渲染。

比 `ClauseBrief` 多出的：`text`（不截断的全文）、`doc_type`、`domain`、`org`、`doc_number`、`publish_date`、`expire_date`、`fetched_at`。

`doc_number` / `source_url` 允许是 `null`：本件是「法律」，不带文号；158 件里也只有 20 件在正文印了官方 URL（[SPEC.md](SPEC.md) §5.3）。前端得能渲染空值，别拿它拼字符串。

查不到 → `40401`。注意 `clause_id` 里的机构前缀是 `PBC-001` 这种无年份格式，**不是** `PBC-2023-001`。

## 4. GET /api/v1/clause/{clause_id}/related — 条文关联查询

### 请求

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|---|
| `clause_id` | path | string | 是 | — | — |
| `top_k` | query | int | 否 | 5 | 1..20 |

对 `RagEngine.get_related_clauses(clause_id, top_k)`。

### 响应

```json
{
  "code": 0, "message": "ok",
  "data": {
    "clause_id": "PBC-001-A53",
    "related": [
      { "clause_id": "PBC-001-A54", "doc_title": "…", "clause_no": "第五十四条",
        "snippet": "…", "score": 0.88, "relation_type": "同章",
        "status": "有效", "source_url": "https://…" }
    ]
  }
}
```

`related[]` 在 `ClauseBrief` 基础上多一个 `relation_type`：

| `relation_type` | 含义 |
|---|---|
| `同条` | 超长条二次切分产生的兄弟片（`第X条-1` / `第X条-2`） |
| `同章` | 同一 `chapter` 下的相邻条文 |
| `引用` | 本条正文援引了对方 |
| `相似` | 纯向量相似度，无结构关系 |

**`relation_type` 是 P3 前端分组展示的依据**（差异化要求③）。

## 5. GET /api/v1/meta/filters — 筛选字典

无请求参数。P2 供数据，P3 实现。

```json
{
  "code": 0, "message": "ok",
  "data": {
    "domain": [ {"value": "证券", "label": "证券", "count": 56},
                {"value": "银行", "label": "银行", "count": 56},
                {"value": "综合", "label": "综合", "count": 22},
                {"value": "保险", "label": "保险", "count": 14},
                {"value": "外汇", "label": "外汇", "count": 11} ],
    "status": [ {"value": "有效", "label": "有效", "count": 155},
                {"value": "尚未生效", "label": "尚未生效", "count": 3},
                {"value": "已废止", "label": "已废止", "count": 1} ],
    "org":    [ {"value": "中国人民银行", "label": "中国人民银行", "count": 75} ],
    "doc_type": [ {"value": "规范性文件", "label": "规范性文件", "count": 63},
                  {"value": "部门规章", "label": "部门规章", "count": 51} ],
    "topic_tags": [ {"value": "债券市场", "label": "债券市场", "count": 20},
                    {"value": "跨境与外汇", "label": "跨境与外汇", "count": 20} ],
    "date_range": { "min": 19930301, "max": 20270301 }
  }
}
```

**上面的 `count` 都是按 159 份实算的**（`domain` 五值合计 159，`doc_type` 七值合计 159）。示例别再随手编数字；语料扩充后跑 `python 脚本/_counts.py` 拿新数。

| 字段 | 类型 | 说明 |
|---|---|---|
| `domain` / `status` / `org` / `doc_type` / `topic_tags` | array | 每项 `{value, label, count}`。`count` 供前端在筛选项旁显示条数 |
| `date_range` | object | `{min, max}`，int `YYYYMMDD`，取自 `effective_date` 的实际范围。当前 150 份有值 |

`count` 的口径：**按「条文数」还是「文档数」统计，第 4 周定，P2 与 P3 必须一致**。当前数据里 `status` 的 155/3/1 是文档数口径，若前端要条文数口径，P2 需另出一份统计。
（`clause_count` 合计 8036，但入库条文是 8156 条 —— 超长条二次切多出 120 条，口径别混。）

`org` 取值必须与 SPEC 第 5 列完全一致（顿号 `、` 分隔的多机关要拆开单项返回，否则前端筛不出来）。
**当前数据 `org` 有同一机关多种写法**的问题（`中国证券监督管理委员会` 28 份 / `中国证监会` 4 份 / `证监会` 2 份；`国家发展改革委` 4 份 / `国家发展和改革委员会` 2 份 / `发展改革委` 1 份 / `中华人民共和国国家发展和改革委员会` 1 份）。P3 拼字典前先按**全称**归一，否则下拉里同一部门会冒出好几个条目。见 [README.md](README.md) §4。

### `topic_tags` 这一轴的来路

第 6 步新加的轴，P2 供数：22 个**业务条线**标签，159 份每份 1~3 个，逗号分隔存在元数据表的第 17 列。
`domain` 只有 5 个粗值（看「归谁管」），`topic_tags` 是正交的细轴（看「讲什么事」）——「反洗钱」「房地产金融」「跨境」这类问法只能靠它筛。
词表、定义与逐份回标见 `产出/topic_tags_词表.md`。

**注意这是新加的，P3 的 `meta/filters` 与前端筛选器要做进去才可用**；在实现前这 159 份标签是没人读的死数据。

## 6. /api/v1/history — 问答历史

### GET

| 参数 | 位置 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | query | string | — | 必填 |
| `page` | query | int | 1 | — |
| `size` | query | int | 10 | 1..50 |

```json
{
  "code": 0, "message": "ok",
  "data": {
    "total": 12, "page": 1, "size": 10,
    "items": [
      { "qa_id": 10241, "question": "商业银行核心一级资本充足率不得低于多少",
        "answer": "……百分之五……", "score": 0.87,
        "created_at": 20261026153012,
        "sources": [ { "clause_id": "PBC-001-A53", "clause_no": "第五十三条",
                       "doc_title": "商业银行资本管理办法" } ] }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `qa_id` | int | `qa_history` 主键，收藏接口用它 |
| `created_at` | int | `YYYYMMDDHHMMSS`，14 位。**注意与日期字段不同**，这是时间戳不是日期 |
| `sources[]` | array | 精简引用，只需渲染「引用了哪几条」 |

### DELETE

`DELETE /api/v1/history?session_id=…` 清空该会话全部历史 → `data: {"deleted": 12}`。
`DELETE /api/v1/history?qa_id=10241` 删单条 → `data: {"deleted": 1}`。
两个参数**必须传且只传一个**，都传或都不传 → `40001`。删除不存在的 `qa_id` → `data: {"deleted": 0}`，不报错。

## 7. /api/v1/favorite — 收藏

### GET

`GET /api/v1/favorite?page=1&size=10`（+ 可选 `clause_id` 精确查）
→ `data: {total, page, size, items[{favorite_id, clause_id, doc_title, clause_no, created_at}]}`

### POST

```json
{ "clause_id": "PBC-001-A53", "qa_id": null }
```

`clause_id` 与 `qa_id` **二选一，必须给一个**。都缺 → `40001`。
→ `data: {"favorite_id": 88}`。重复收藏同一条 → 幂等返回**已存在的** `favorite_id`，不报错、不产生第二行。

### DELETE

`DELETE /api/v1/favorite?favorite_id=88` → `data: {"deleted": 1}`。
删不存在的 → `data: {"deleted": 0}`。

## 8. GET /api/v1/health — 健康检查

无参数。第 5 周就要有，是 P3 最早的联调入口。

```json
{
  "code": 0, "message": "ok",
  "data": {
    "service": "ok",
    "mysql":   { "status": "ok", "latency_ms": 3 },
    "milvus":  { "status": "ok", "latency_ms": 8, "collection": "finance_clause", "row_count": 8156 },
    "version": "0.1.0"
  }
}
```

任一依赖不通 → HTTP `503` + `code 50301`，`data` 里该项 `status: "down"`、`error` 带原因。

`row_count` 取自 Milvus，用于确认知识库确实是 **8156** 行而不是空库——**第 10 周「知识库搭建完成」的关键证据**，空库不报错是本项目最大的隐性风险（[DEPLOY.md](DEPLOY.md) §5）。

> 8156 是**入库行数**（超长条二次切后的 chunk 数），不是锚点数。锚点数 8036 由 [SPEC.md](SPEC.md) §6 校验，两者差 120 全是超长条二次切造成的。数字变了以 `python 脚本/pipeline.py --all` 重跑为准。