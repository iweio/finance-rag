"""契约交叉校验：五份契约里的字段名必须同源。

改任何字段名后跑一次。任一 FAIL 都说明有契约已经和别的契约脱节。
"""
import re
import os

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
read = lambda p: open(os.path.join(ROOT, p), encoding="utf-8").read()

_ws = openpyxl.load_workbook(os.path.join(ROOT, "产出", "语料元数据.xlsx")).active
XLSX_COLS = [c.value for c in _ws[1]]
# doc_id → {列名: 值}，第 9 项预检要拿元数据列（org/doc_title/chapter…）比对字节长度
XLSX_BY_DOC = {r[1]: dict(zip(XLSX_COLS, r)) for r in _ws.iter_rows(min_row=2, values_only=True) if r[1]}

spec = read("契约/SPEC.md")
api = read("契约/API.md")
db = read("契约/DB.md")
mod = read("契约/MODULE.md")
rm = read("契约/README.md")

fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


# ---- 1. SPEC §2 列定义 == xlsx 实际列名，逐位 ----
sec2 = spec[spec.find("## 2. 列定义"):spec.find("### 2.1")]
SPEC_COLS = re.findall(r"^\|\s*\d+\s*\|\s*`([^`]+)`", sec2, re.M)
check("SPEC §2 列 == xlsx 列（逐位）", SPEC_COLS == XLSX_COLS,
      f"{len(SPEC_COLS)} vs {len(XLSX_COLS)}"
      + ("" if SPEC_COLS == XLSX_COLS else
         f" 首处不符 {[(a, b) for a, b in zip(SPEC_COLS, XLSX_COLS) if a != b][:3]}"))

# ---- 2. DB.md knowledge_point 列名是 SPEC 列名的子集 ----
kp = db[db.find("CREATE TABLE `knowledge_point`"):]
kp = kp[:kp.find("ENGINE=InnoDB")]
KP_COLS = re.findall(r"^\s+`([a-z_]+)`\s+(?:BIGINT|INT|VARCHAR|TEXT|FLOAT|JSON|DATETIME)", kp, re.M)
stray = [c for c in KP_COLS if c not in SPEC_COLS
         and c not in ("clause_id", "clause_no", "clause_index", "chapter", "text")]
check("knowledge_point 列名不越界", not stray, f"自带 {stray[:4]}")

# ---- 3. API §1.1 ClauseBrief 字段 ----
sec = api[api.find("### 1.1"):api.find("## 2. POST")]
BRIEF = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", sec, re.M)
brief_extra = [c for c in BRIEF if c not in SPEC_COLS and c not in ("clause_id", "clause_no", "clause_index", "chapter", "snippet", "score")]
check("ClauseBrief 字段不越界", not brief_extra, f"自带 {brief_extra}")

# ---- 4. MODULE ClauseDetail 字段 == SPEC 列 ∪ {派生字段} ----
sec = mod[mod.find("class ClauseDetail"):mod.find("class SearchResult")]
DETAIL = re.findall(r"^\s{4}([a-z_]+):", sec, re.M)
DERIVED = {"clause_id", "clause_no", "clause_index", "chapter", "text"}
detail_extra = [c for c in DETAIL if c not in SPEC_COLS and c not in DERIVED]
missing = [c for c in SPEC_COLS if c not in DETAIL and c not in ("序号", "file_name", "clause_count", "clause_scheme", "source_dir", "note", "topic_tags")]
check("ClauseDetail 覆盖 SPEC 元数据列", not missing and not detail_extra,
      f"缺 {missing} 多 {detail_extra}")

# ---- 5. API §3 响应 JSON 字段 == ClauseDetail ----
# 只比 data 里的字段，不比 code/message/data 这层响应包
j = api[api.find("## 3. GET"):api.find("## 4. GET")]
API3 = re.findall(r'"([a-z_]+)"\s*:', j[j.find('"data": {') + len('"data": {'):])
check("API §3 响应 == ClauseDetail", set(API3) == set(DETAIL),
      f"差集 {sorted(set(API3) ^ set(DETAIL))}")

# ---- 6. Milvus schema 字段名不越界 ----
mv = db[db.find("fields = ["):db.find("schema = CollectionSchema")]
MV = re.findall(r'FieldSchema\("([a-z_]+)"', mv)
mv_extra = [c for c in MV if c not in SPEC_COLS and c not in
            ("clause_id", "embedding", "text", "embed_text", "clause_no", "chapter", "clause_index", "is_short")]
check("Milvus 字段不越界", not mv_extra, f"自带 {mv_extra}")

# ---- 7. QUERY_PREFIX 三处同字 ----
QP = "为这个句子生成表示以用于检索相关文章："
where = [n for n, t in (("MODULE", mod), ("DEPLOY", read("契约/DEPLOY.md")),
                        ("全流程", read("文档/全流程说明.md"))) if QP in t]
check("QUERY_PREFIX 出现在 MODULE/DEPLOY/全流程", len(where) == 3, f"命中 {where}")

# ---- 8. 四个枚举值一致 ----
DOM = "银行"
for name, token in (("尚未生效", "尚未生效"), ("已修订", "已修订")):
    check(f"枚举 `{token}` 在 SPEC 与 README 均有记录",
          token in spec and token in rm)

# ---- 9. Milvus max_length 够装真实数据（按 UTF-8 字节，不是字符）----
#
# 这条是被真事逼出来的：`org` 原写 128，实测 4 份联合发文最长 174 字节。
# 两边口径不同 —— Milvus 的 max_length 是**字节**，MySQL 的 VARCHAR(n) 是**字符**，
# 中文一字 3 字节，128 字节只装 42 个汉字。只看 MySQL 那边永远发现不了。
import glob
import json

LIM = {m[0]: int(m[1]) for m in re.findall(
    r'FieldSchema\("([a-z_]+)",\s+DataType\.VARCHAR,\s+max_length=(\d+)', mv)}

chunks = []
for fp in sorted(glob.glob(os.path.join(ROOT, "产出", "chunks", "*.jsonl"))):
    for line in open(fp, encoding="utf-8"):
        if line.strip():
            chunks.append(json.loads(line))

if not chunks:
    check("Milvus max_length 预检", False, "产出/chunks 为空，先跑 pipeline.py --all")
else:
    over = []
    for field, lim in LIM.items():
        worst = 0
        for c in chunks:
            raw = c.get(field) if field in c else XLSX_BY_DOC.get(c["doc_id"], {}).get(field)
            worst = max(worst, len(str(raw or "").encode("utf-8")))
        if worst > lim:
            over.append(f"{field} {worst}>{lim}")
    check(f"Milvus max_length 够装 {len(chunks)} 条真实数据（按字节）", not over,
          f"超限 {over}" if over else f"最紧的是 {min(LIM, key=lambda f: LIM[f] - max(len(str(c.get(f) or '').encode('utf-8')) for c in chunks))}")

# ---- 10. 版本号三处必须一致 ----
#
# 版本号写散在几个文件里就会漂。这类漂移**不报错**，只表现为
# 「照 DEPLOY 装了 2.4 却连不上 2.5 的库」，或「pymilvus 太老，nullable 被静默吞掉」。
compose = read("docker-compose.milvus.yml")
dep_tag = sorted(set(re.findall(r"milvusdb/milvus:(v[\d.]+)", read("契约/DEPLOY.md"))))
cmp_tag = sorted(set(re.findall(r"milvusdb/milvus:(v[\d.]+)", compose)))
check("Milvus 镜像 tag：DEPLOY §2 表 == docker-compose.milvus.yml", dep_tag == cmp_tag,
      f"DEPLOY {dep_tag} vs compose {cmp_tag}")

# pymilvus 区间：DB.md §3.1.2 与 DEPLOY.md §2.2 都要写 >=2.5,<2.6（2.4 不支持 nullable）
#
# **逐文件分开查**，不把两文件拼起来数总数 —— 拼起来时 DB.md 那句被删光、
# 只剩 DEPLOY.md 自己的两处，总数照样 >=2 会放行（负向测试踩到）。
# 比的是**完整区间**不只看下界：`all(m == "2.5")` 时上界被改成 `<2.5` 也会放行。
REQ = ("2.5", "2.6")
for name, text in (("DB.md §3.1.2", db), ("DEPLOY.md §2.2", read("契约/DEPLOY.md"))):
    req = re.findall(r"pymilvus>=([\d.]+),<([\d.]+)", text)
    check(f"pymilvus 区间：{name} 写的是 >=2.5,<2.6",
          bool(req) and all(r == REQ for r in req), f"命中 {req}")

print()
print("### " + (f"{len(fails)} 项未过：{fails}" if fails else "契约交叉校验全部通过"))