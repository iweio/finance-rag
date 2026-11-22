"""图 schema：六类节点、八类边、ID 规则。

和 `文档/知识图谱与GNN方案.md` 一一对应。**改这里必须同步改文档与 `kg/README.md`**，
因为训练脚本、导出脚本、API 都按这些字符串判类型。
"""
from __future__ import annotations

# ---- 节点类型 ----
CLAUSE = "Clause"          # 条文（8156）
CHAPTER = "Chapter"        # 章节（doc_id + chapter 文本）
DOC = "Doc"                # 法规（159）
AGENCY = "Agency"          # 发文机关（23）
TAG = "TopicTag"           # 业务条线标签（22）
EXTERNAL = "ExternalLaw"   # 库外被引法规（《公司法》等）

NODE_TYPES = (CLAUSE, CHAPTER, DOC, AGENCY, TAG, EXTERNAL)

# ---- 边类型（src -> dst）----
NEXT = "NEXT"                    # Clause -> Clause 同文档相邻
IN_CHAPTER = "IN_CHAPTER"        # Clause -> Chapter
IN_DOC = "IN_DOC"                # Clause/Chapter -> Doc
CITES = "CITES"                  # Clause -> Clause 显式引用且能解析到库内条文
MENTIONS_DOC = "MENTIONS_DOC"    # Clause -> Doc   正文提到库内法规
MENTIONS_EXT = "MENTIONS_EXT"    # Clause -> ExternalLaw
CITES_DOC = "CITES_DOC"          # Doc -> Doc      法规之间的引用（正文提及聚合）
ISSUED_BY = "ISSUED_BY"          # Doc -> Agency
HAS_TAG = "HAS_TAG"              # Doc -> TopicTag

EDGE_TYPES = (
    (CLAUSE, NEXT, CLAUSE),
    (CLAUSE, IN_CHAPTER, CHAPTER),
    (CLAUSE, IN_DOC, DOC),
    (CHAPTER, IN_DOC, DOC),
    (CLAUSE, CITES, CLAUSE),
    (CLAUSE, MENTIONS_DOC, DOC),
    (CLAUSE, MENTIONS_EXT, EXTERNAL),
    (DOC, CITES_DOC, DOC),
    (DOC, ISSUED_BY, AGENCY),
    (DOC, HAS_TAG, TAG),
)

# 服务层展示顺序（前端图例）
EDGE_LABELS = {
    NEXT: "相邻条文",
    IN_CHAPTER: "所属章节",
    IN_DOC: "所属法规",
    CITES: "引用条文",
    MENTIONS_DOC: "提及法规",
    MENTIONS_EXT: "提及库外法规",
    CITES_DOC: "法规引用",
    ISSUED_BY: "发文机关",
    HAS_TAG: "主题标签",
}


def clause_id(cid: str) -> str:
    return f"C:{cid}"


def chapter_id(doc_id: str, chapter: str) -> str:
    return f"CH:{doc_id}#{chapter.strip()}"


def doc_id(doc_id_: str) -> str:
    return f"D:{doc_id_}"


def agency_id(name: str) -> str:
    return f"A:{name.strip()}"


def tag_id(name: str) -> str:
    return f"T:{name.strip()}"


def external_id(title: str) -> str:
    return f"E:{title.strip()}"


def split_id(node_id: str) -> tuple[str, str]:
    """'C:PBC-001-A53' -> ('Clause', 'PBC-001-A53')"""
    prefix, _, rest = node_id.partition(":")
    kind = {"C": CLAUSE, "CH": CHAPTER, "D": DOC, "A": AGENCY, "T": TAG, "E": EXTERNAL}.get(prefix)
    if kind is None:
        raise ValueError(f"未知节点 ID: {node_id}")
    return kind, rest
