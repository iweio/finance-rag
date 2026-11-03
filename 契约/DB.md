# DB 契约

两个存储：**MySQL**（业务数据，P1 定表结构、P3 写 ORM）与 **Milvus**（条文向量，P2 建库、P1 查询）。来源《技术方案》§6.1/§8.1 与《分工》sheet `6-模块间接口边界`。

**关键约束：Milvus 的标量字段名、MySQL 的列名、API 的响应字段名，三者与 [SPEC.md](SPEC.md) §2 的 19 个列名共用同一套叫法。** 改一处要改三处。

## 1. 分工与冻结

| 库 | 建库方 | 使用方 | 冻结时间 |
|---|---|---|---|
| MySQL 5 张表 | P1 定 DDL | P3 写 ORM 与 CRUD | 第 4 周 |
| Milvus `finance_clause` | P2 | P1 只写查询，不重建索引 | 第 6 周 |

字段变更需**双方确认后同步本文档**。P1 不得私自改 Milvus schema，P3 不得私自改 MySQL DDL。

## 2. MySQL

### 2.1 库与连接

```sql
CREATE DATABASE rag_finance DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

连接参数见 [DEPLOY.md](DEPLOY.md) §3。**字符集必须 `utf8mb4`**：条文里有生僻字（`〇`、异体字），`utf8` 存不进去且不报错。

### 2.2 五张表

```sql
-- 用户
CREATE TABLE `user` (
  `user_id`    BIGINT       NOT NULL AUTO_INCREMENT,
  `username`   VARCHAR(64)  NOT NULL,
  `password`   VARCHAR(128) NOT NULL COMMENT 'bcrypt 哈希，不存明文',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 会话
CREATE TABLE `session` (
  `session_id` VARCHAR(64)  NOT NULL COMMENT '前端生成，如 sess_7f3a91c2',
  `user_id`    BIGINT       NULL,
  `title`      VARCHAR(128) NULL COMMENT '取首问前 30 字',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`session_id`),
  KEY `idx_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 问答历史
CREATE TABLE `qa_history` (
  `qa_id`      BIGINT       NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64)  NOT NULL,
  `question`   TEXT         NOT NULL,
  `answer`     MEDIUMTEXT   NOT NULL COMMENT '完整答案，可能含 markdown',
  `sources`    JSON         NULL COMMENT '[{"clause_id","clause_no","doc_title"}]',
  `score`      FLOAT        NULL COMMENT '可信度 0..1',
  `created_at` DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`qa_id`),
  KEY `idx_session_created` (`session_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 收藏
CREATE TABLE `favorite` (
  `favorite_id` BIGINT      NOT NULL AUTO_INCREMENT,
  `user_id`     BIGINT      NULL,
  `clause_id`   VARCHAR(64) NULL,
  `qa_id`       BIGINT      NULL,
  `created_at`  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`favorite_id`),
  UNIQUE KEY `uk_clause` (`user_id`, `clause_id`),
  UNIQUE KEY `uk_qa`     (`user_id`, `qa_id`),
  KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 知识点（与 Milvus 的 clause 一一对应，存业务侧引用）
CREATE TABLE `knowledge_point` (
  `clause_id`      VARCHAR(64)  NOT NULL COMMENT '与 Milvus 主键同值，如 PBC-001-A53',
  `doc_id`         VARCHAR(32)  NOT NULL,
  `doc_title`      VARCHAR(256) NOT NULL,
  `clause_no`      VARCHAR(64)  NOT NULL COMMENT '第五十三条',
  `clause_index`   INT          NOT NULL COMMENT '53，排序用',
  `chapter`        VARCHAR(256) NULL,
  `text`           TEXT         NOT NULL,
  `doc_type`       VARCHAR(32)  NOT NULL,
  `domain`         VARCHAR(16)  NOT NULL,
  `org`            VARCHAR(128) NOT NULL,
  `doc_number`     VARCHAR(128) NULL,
  `publish_date`   INT          NULL COMMENT 'YYYYMMDD',
  `effective_date` INT          NULL COMMENT 'YYYYMMDD',
  `expire_date`    INT          NULL COMMENT 'YYYYMMDD',
  `status`         VARCHAR(16)  NOT NULL,
  `source_url`     VARCHAR(512) NULL,
  `fetched_at`     INT          NOT NULL COMMENT 'YYYYMMDD',
  PRIMARY KEY (`clause_id`),
  KEY `idx_doc`     (`doc_id`),
  KEY `idx_status`  (`status`),
  KEY `idx_domain`  (`domain`),
  KEY `idx_effdate` (`effective_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2.3 列名对齐说明

`knowledge_point` 的列名是 **14 个 SPEC 列名的直接映射**，逐字对齐：

| SPEC 列 | `knowledge_point` 列 | 类型 | 备注 |
|---|---|---|---|
| — | `clause_id` | VARCHAR(64) | Milvus 主键同值 |
| `doc_id` | `doc_id` | VARCHAR(32) | — |
| `doc_title` | `doc_title` | VARCHAR(256) | — |
| — | `clause_no` | VARCHAR(64) | 原文锚点原文 |
| — | `clause_index` | INT | 同 技术方案 §6.1 |
| — | `chapter` | VARCHAR(256) | 同 技术方案 §6.1 |
| `doc_type` | `doc_type` | VARCHAR(32) | — |
| `domain` | `domain` | VARCHAR(16) | 技术方案写 VARCHAR，此处收窄到 16，实测最长 2 字 |
| `org` | `org` | VARCHAR(128) | — |
| `doc_number` | `doc_number` | VARCHAR(128) | — |
| `publish_date` | `publish_date` | INT | `YYYYMMDD` |
| `effective_date` | `effective_date` | INT | `YYYYMMDD` |
| `expire_date` | `expire_date` | INT | `YYYYMMDD` |
| `status` | `status` | VARCHAR(16) | — |
| `source_url` | `source_url` | VARCHAR(512) | — |
| `fetched_at` | `fetched_at` | INT | `YYYYMMDD` |

**日期为什么是 `INT` 而不是 `DATE`**：要与 Milvus 的标量字段保持同一类型，否则 P1 在两边拼过滤条件时要来回转换；且 `INT` 的范围比较 `effective_date <= 20241019` 在两边写法一致（技术方案 §6.1）。

**`favorite` 的两个 UNIQUE 用 `NULL` 的语义**：MySQL 的 UNIQUE 允许多行为 `NULL`，所以 `uk_clause` 只约束「同一用户同一条文只能收藏一次」，`qa_id` 为 `NULL` 的行不受它影响，反之亦然。这正是 §7 收藏接口幂等语义的落点。

## 3. Milvus：`finance_clause`

### 3.1 Schema

```python
from pymilvus import CollectionSchema, FieldSchema, DataType

fields = [
    FieldSchema("clause_id",      DataType.VARCHAR,       max_length=64,  is_primary=True),
    FieldSchema("embedding",      DataType.FLOAT_VECTOR,  dim=1024),
    # ---- 文本：两个字段必须分开存 ----
    FieldSchema("text",           DataType.VARCHAR,       max_length=8192),
    FieldSchema("embed_text",     DataType.VARCHAR,       max_length=8192),
    # ---- 标量：过滤与溯源 ----
    FieldSchema("doc_id",         DataType.VARCHAR,       max_length=32),
    FieldSchema("doc_title",      DataType.VARCHAR,       max_length=256),
    FieldSchema("doc_type",       DataType.VARCHAR,       max_length=32),
    FieldSchema("clause_no",      DataType.VARCHAR,       max_length=64),
    FieldSchema("chapter",        DataType.VARCHAR,       max_length=256, nullable=True),
    # org 取 256（不是 128）而不是「够用就行」：定这个值时有联合发文实测 174 字节。
    # 2026-10-27 把简称归一成全称后又涨了一截 —— 最长 `PBC-030` 225 字节，**余量只剩 31**。
    # 再往库里加「联署机关更多」的件就要重新算，不能默认还有富余。
    FieldSchema("org",            DataType.VARCHAR,       max_length=256),
    # nullable 的取据：实测章为空 1132/8156、doc_number 空 22/159、
    # publish_date 空 11/159、effective_date 空 9/159、source_url 空 18/159、
    # expire_date 空 159/159。语料一变就要重跑 脚本/_counts.py 重新取数。
    # Milvus 的非主键字段**默认不接受 None**，不加 nullable 会在 insert 时整批失败。
    FieldSchema("doc_number",     DataType.VARCHAR,       max_length=128, nullable=True),
    FieldSchema("source_url",     DataType.VARCHAR,       max_length=512, nullable=True),
    FieldSchema("status",         DataType.VARCHAR,       max_length=16),
    FieldSchema("domain",         DataType.VARCHAR,       max_length=16),
    FieldSchema("topic_tags",     DataType.VARCHAR,       max_length=256, nullable=True),
    FieldSchema("publish_date",   DataType.INT64,         nullable=True),
    FieldSchema("effective_date", DataType.INT64,         nullable=True),
    FieldSchema("expire_date",    DataType.INT64,         nullable=True),
    FieldSchema("clause_index",   DataType.INT64),
    FieldSchema("is_short",       DataType.BOOL),
    FieldSchema("fetched_at",     DataType.INT64),
]
schema = CollectionSchema(fields, description="金融制度条文库")
```

字段与 技术方案 §8.1 完全一致，仅补了两个 SPEC 有而 §8.1 未列的：`doc_type`（前端筛选用）、`fetched_at`（合规留痕用）。

### 3.1.1 `max_length` 是**字节**不是字符（`org` 因此从 128 提到 256）

Milvus 的 `max_length` 按 **UTF-8 字节**计，MySQL 的 `VARCHAR(n)` 按**字符**计。同一个 `128`，两边不是一回事：中文一字 3 字节，128 字节只装得下 42 个汉字。

实测踩到：`org` 限 128 字节，**4 份联合发文超限，最长 174 字节**

```
中国人民银行、国家发展和改革委员会、商务部、国务院国有资产监督管理委员会、
中国银行保险监督管理委员会、国家外汇管理局                    ← 58 字 / 174 字节
```

这 4 份是 7~8 个机关联合发文，`org` 本来就长，**不能截断**（[API.md](API.md) §5 要求顿号分隔的多机关拆开单项返回，截了就筛不出来）。故 Milvus 侧提到 **256 字节**；MySQL 侧 `VARCHAR(128)` 存的是 58 个字符，**不用改**。

入库前必须过一遍字节预检（`脚本/ingest.py` 的第一道闸门），逐字段比对本文档的 `max_length`。**实测余量**：

| 字段 | 限长(字节) | 实测最大 | 余量 |
|---|---|---|---|
| `org` | 256 | 174 | 82 |
| `chapter` | 256 | 250 | 6 ★ |
| `doc_title` | 256 | 239 | 17 ★ |
| `status` | 16 | 12 | 4 |
| `text` / `embed_text` | 8192 | 2952 / 3204 | 充裕 |
| 其余 | — | 远低于限长 | — |

★ `chapter` 与 `doc_title` 余量已不足 20 字节。当前 159 份不超限，但**新增文档前要重跑这个预检**。

### 3.1.2 为空的字段必须标 `nullable=True`

Milvus 的非主键字段**默认不接受 `None`**，硬塞会在 insert 时整批失败。实测空值分布：

| 字段 | 空 | 说明 |
|---|---|---|
| `expire_date` | **159 / 159** | 不是「没有废止件」——`status=已废止` 有 1 份（`PBC-011`）。是**本库没有废止日这个字段**，废止信息只在正文里 |
| `chapter` | **1132 / 8156** | 无章节体例的文件（「一、二、三」「X.Y.Z」两类）。语料 +1 份而空值没动 —— `NFRA-016` 有章 |
| `source_url` | **18 / 159** | ★ 见 §3.1.3 —— 已从 138 补到 18；剩下的仍有据 |
| `doc_number` | 22 / 159 | 交易所自律规则等无发文字号（[SPEC.md](SPEC.md) §5.1） |
| `publish_date` | 11 / 159 | — |
| `effective_date` | 9 / 159 | — |
| `topic_tags` | ~~158 / 158~~ **0 / 159** | 第 6 步已回标满（2026-10-27）。字段仍标 `nullable=True` —— 是为**扩充语料**留的，新件回标前会是空 |

故 `chapter` / `doc_number` / `source_url` / `topic_tags` / `publish_date` / `effective_date` / `expire_date`
七个字段标 `nullable=True`。

**★ 但 2.4 线做不到，而且不报错。** 原先这里写的是「Milvus 2.4+」，实测这句话是错的：

`pymilvus 2.4.x` 的 `FieldSchema` 签名是 `(self, name, dtype, description='', **kwargs)`，
`nullable` 被 `**kwargs` **静默吞掉** —— 建表不报错，但服务端记下来的是 `nullable=None`：

```
chapter       nullable=None   params={'max_length': 256}    ← nullable 没了
source_url    nullable=None   params={'max_length': 512}
status        nullable=None   params={'max_length': 16}
```

根因在客户端的打包层。`pymilvus/client/entity_helper.py` 里 `valid_data` / `nullable` 的出现次数：

| 版本 | 次数 |
|---|---|
| 2.4.15 | **0** —— 空值位图根本没实现 |
| 2.5.18 | **23** |

后果是**行式、列式两条插入路径都拒 `None`**，报
`field (chapter) expect string input, got: <class 'NoneType'>`，从这句看不出真正的原因。

**实测对照（2025-10-27 本机）**

| | Milvus 2.4.15 + pymilvus 2.4.15 | Milvus 2.5.27 + pymilvus 2.5.18 |
|---|---|---|
| 服务端记录 `nullable` | ❌ `nullable=None` | ✅ `nullable=True` |
| 行式字典插 `None` | ❌ expect string | ✅ 通过 |
| 列式插 `None` | ❌ expect string | 未测（已统一改行式） |
| nullable 字段建 INVERTED | 未测到 | ✅ 通过 |
| 空值回读 | — | ✅ 仍是 `None` |

**故 Milvus 的版本要求是 `v2.5.27` + `pymilvus>=2.5,<2.6` 一对**（[DEPLOY.md](DEPLOY.md) §2.2）。
`脚本/ingest.py` 里有版本闸门，客户端低于 2.5 直接退出并给出能照做的安装命令。

**`effective_date` 是唯一「可为空 + 被 INVERTED 索引」的字段**，2.5.27 上实测不冲突。

**哨兵值方案不再需要**（字符串 `""`、日期 `0` 那一套）。它能跑，但要三个人永远记住
`0 会被 effective_date <= … 当成真日期` 这类约定 —— 谁忘了就出错，且不报错。

### 3.1.3 `source_url` 补到 141/159（原缺口 138 份）

补之前 138/158 为空，只有 20 份有值 —— **不满足 技术方案 §1.4「逐条记录 `source_url` 与采集时间」**
（`fetched_at` 是 158 份全有的，缺的只是 URL）。**2026-10-27 补到 141/159，空值降到 18。**

| | 份数 | 来源 |
|---|---|---|
| 文件正文/页脚自带 | 20 | 打印网页时带下来的 URL，真抽取 |
| 联网查证官方页 | 120 | 按 `doc_title` + `doc_number` 在官方站定位 |
| 采集当时就记下 | 1 | `NFRA-016`，见 [README.md](README.md) §4 #5 |
| **合计有值** | **141 / 159 = 88.7%** | 逐行在 `note` 里标了来源 |
| 留空 | 18 | 见下 |

> **空值数没变（还是 18），分母变了（158 → 159）。** 别看到「还是 18」就以为这一节没动过 ——
> 新加的那份自带 URL，所以补进来的是分子不是分母里的空。

**留空的 18 份不是没查，是分两类：**

- **7 份查到了官方页但版本与入库件不一致**，主动丢弃：`SPC-006/007`（官方页是 2013/2018
  首发版，本库是 2020 修正版）、`CSRC-003/004/017/024`（官方页停在旧的令号修订）、
  `GOV-010`（**白名单域名上只有 2006 年原版，本库是 2019 年第三次修订版，根本没有对应版本的页**）。
  **指向旧版比留空更坏**，`note` 里写明原因，裁决理由逐条记在 `脚本/_merge_urls.py` 的 `HOLD` 常量。
- **11 份官方站确实没找到**：`BSE-001` `NFRA-004/005/008/009/010/015` `PBC-015`
  `SPC-002/003/005`。

**注意 12 部分 NPC 法律是「本表 `doc_number` 为空」才被代理降成 `title-only` 的**，
不是版本存疑；比对入库正文头的修正沿革后全部接收（详见 [SPEC.md](SPEC.md) §8.3）。

**条文行口径**：`source_url != ""` 的条文 **7092 / 8156 = 87.0%**，
即 API 的溯源卡片（[API.md](API.md) §1.1 `ClauseBrief.source_url`）对约 **13.0%** 的条文
显示不出原文链接 —— 前端仍要能优雅处理「有 `doc_title` + `clause_no` 但没 URL」这种情况。
**注意这两口径不等价**：某份文件有 URL，它下面每条条文的这一列才有值，
但留空的 18 份里有几份是长文件（`BSE-001` 等），所以文件口径 88.7% 高于条文口径 87.0%。

**这条不是数据缺口了，是取证方式决定的覆盖率。** 补法是按 `doc_title` + `doc_number` 回官网
逐份定位；不变式是**版本一致**才填，详见 [SPEC.md](SPEC.md) §8.3。

### 3.1.4 ~~语料里没有《商业银行资本管理办法》~~ —— **2026-10-27 已补上**

> **本节记的是一个已经修掉的缺口，留着是因为它是一次真实的验收发现。**
> `NFRA-016`《商业银行资本管理办法》已入库（`clause_count` 206，`publish_date` 20231026，
> `effective_date` 20240101，`source_url` 见 [README.md](README.md) §4 #15）。
> 下面那个探针现在的结果是：**命中 1 份文件 / 1 条 chunk**，答案在 `NFRA-016-A26`。
> 语料 158 → 159、入库 7949 → 8156 就是这一次改出来的。

第 8 步入库后拿真问题验收，发现一个召回不出正确答案的：

```
Q: 商业银行核心一级资本充足率不得低于多少？
   0.6776  NPC-006-A39  《中华人民共和国商业银行法》第三十九条
           （一）资本充足率不得低于百分之八
   …其余 4 条也都不是问的那件事
```

**不是召回差，是语料根本没有这份文件：**

```
元数据里标题含「资本管理办法」的文档: 0
全语料含「核心一级资本充足率」的 chunk: 0
```

158 份里同时有《商业银行法》《保险法》《证券法》，但没有《商业银行资本管理办法》—— 银行业资本监管的基础性规章。
当前语料是采集时「有效文件」目录的快照，**不保证覆盖全部基础性制度**。

与 §3.1.3 的 `source_url` 缺口同类，都是**数据缺口不是设计如此**，走同一套回填流程。
未补之前，涉及资本充足率的提问只能召回到《商业银行法》第三十九条（百分之八那一档），
**答不出核心一级/一级资本充足率**，且系统不会提示「库里没有」。

**补的时候顺手量了一下缺口有多大。** 上面两句探针是「按标题」和「按库内关键词」找的，
所以它只能证明「库里没有」，证明不了「库里还缺哪些」。当前 159 份仍然只是
「有效文件」目录某一天的快照 —— **同一类缺口随时可能还有别的**，验收时该继续拿真问题去撞。

### 3.2 两个文本字段不能合并

| 字段 | 内容 | 用途 |
|---|---|---|
| `text` | 条文原文 | 返回给用户、前端展示 |
| `embed_text` | `《制度名》章节 条款号\n` + 原文 | **只用于生成 `embedding`** |

```
embed_text:  《中华人民共和国商业银行法》 第四章 贷款和其他业务的基本规则 第三十九条
             第三十九条 商业银行贷款，应当遵守下列资产负债比例管理的规定：
             (一)资本充足率不得低于百分之八；…

text:        第三十九条 商业银行贷款，应当遵守下列资产负债比例管理的规定：
             (一)资本充足率不得低于百分之八；…
```

（上面是库里真实的一条，`clause_id` = `NPC-006-A39`。原先这里举的是《商业银行资本管理办法》第五十三条 —— 那份文件**不在当前语料里**，见 §3.1.4。）

只存 `text` 的话，第 12 周想补上下文头就得全库重建（技术方案 §5.4 / 坑 #7）。**两个字段都存，从第一天就分开。**

### 3.3 索引

```python
# 向量：HNSW
client.create_index("finance_clause", "embedding", {
    "index_type": "HNSW", "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200},
})
# 标量：三个 INVERTED
for f in ("status", "domain", "effective_date"):
    client.create_index("finance_clause", f, {"index_type": "INVERTED"})
client.load_collection("finance_clause")
```

`metric_type` 必须是 `COSINE`，与编码时的 `normalize_embeddings=True` 配对（[MODULE.md](MODULE.md) §4）。备选 `IVF_FLAT(nlist=128)`，但 1000+ 条量级不需要调 `nprobe`，用 HNSW。

**不分区**（技术方案 §8.4）：数据量只有 8156 行，按 `domain` 分区没有检索收益，反而增加写入与维护复杂度。

### 3.4 索引必须建在 `status` / `domain` / `effective_date` 上

这三个是**时效性过滤（差异化要求②）**的落点。缺了索引，`filter=` 会退化成全表扫。这是 P2 交付给 P1 的硬条件（《分工》sheet6「标量字段须支持 status 与 effective_date 过滤」）。

## 4. 过滤表达式

```python
# 只取现行有效、且已生效
expr = 'status == "有效" and effective_date <= 20241019'

# 再加业务领域
expr = 'status == "有效" and domain == "银行"'

# 组合筛选（对应 API 的 filters）
expr = 'status == "有效" and domain == "银行" and org == "中国人民银行"'
```

```python
res = client.search(
    collection_name="finance_clause",
    data=[query_vec],
    filter=expr,        # ← 先过滤后检索
    limit=10,
    output_fields=["clause_id", "doc_id", "doc_title", "clause_no",
                   "chapter", "text", "status", "effective_date", "source_url"],
)
```

**`filter=` 是先过滤后检索，不是检索后再筛**（技术方案 §8.3 / 坑 #4）。写成「先 `search` top100，再在 Python 里 `if r.status == "有效"`」，过滤后可能只剩几条，召回严重不足，而且**不报错**——表现为「怎么召回的都不对」。

`output_fields` 要覆盖 [API.md](API.md) §1.1 的 `ClauseBrief` 全部字段，缺字段 P1 就得回查 MySQL，多一次往返，影响 ≤2 秒指标。

**客户端写法统一用 pymilvus 的 ORM（`Collection`），别混用 `MilvusClient`。** 上面 `client.search(...)` 只是示意参数含义（`filter` / `limit` / `output_fields` 这些名字两种客户端一致），落代码时按 ORM 写，与 §5 的写入保持同一套 API —— 否则库里同一件事有两种写法，改一处忘一处。

## 5. 写入流程

用 pymilvus 的 ORM（`脚本/ingest.py`），**每行一个字典**：

```python
from pymilvus import Collection
coll = Collection("finance_clause", schema=build_schema(1024))
for i in range(0, len(rows), 500):
    coll.insert(rows[i:i + 500])        # rows 是 list[dict]，键就是 §3.1 的字段名
coll.flush()
# create_index → load_collection（见 §3.3）
```

**必须行式字典，不能列式 `list[list]`。** pymilvus 对列式只比对字段**个数**、不比对字段**名**（`orm/schema.py` 的 `_check_data_schema_cnt` 只在 DataFrame 分支才查列名）。列顺序写错会把整批行的字段**静默串位**，Milvus 一句错都不报，表现为「检索结果似是而非」。行式字典的键对不上则当场抛异常。

**服务端与客户端必须同为 2.5 线**（§3.1.2）：`Milvus v2.5.27` + `pymilvus>=2.5,<2.6`。

写入后必须**验证**：

```python
coll.query(expr="clause_id != ''", output_fields=["count(*)"])
# 期望 8156 —— 入库行数（超长条二次切后）。锚点数 8036 见 SPEC.md §6
```

**空库不报错是本项目最大的隐性风险**（技术方案 §3.1）：对扫描件直接 `get_text()` 会得到空字符串，程序不报错，直到第 10 周才发现知识库是空的。所以 8156 这个数字要出现在 `/api/v1/health` 的 `row_count` 里（[API.md](API.md) §8），随时可见。

## 6. 增量更新

对应 [README.md](README.md) §4 待裁决 #1。技术方案 §11 定义三种场景：

| 场景 | 触发 | 做法 |
|---|---|---|
| 新文档入库 | 采集到新制度 | 同一条流水线；按 `clause_id` 去重，存在则 update，否则 insert |
| 效力状态变更 | 每 2 周定期核查 | 按 `clause_id` 批量 update `status` / `expire_date` |
| 文档被修订 | 发现新版本 | 旧版 `status = 已修订`，新版按新 `doc_id` 入库，**两者都留**以便溯源 |

第三种场景要求 `已修订` 成为合法枚举值，但当前 159 行里没有——因为 SPEC §1 只收 `有效文件/` 目录，`旧版(已被修订)` 整目录不收。**这两条规则并存时以哪条为准，第 11 周前必须裁决**（[README.md](README.md) §4）。

```bash
python -m kb.ingest --path data/raw/PBC-002/     # 建议做成 CLI，便于演示
```