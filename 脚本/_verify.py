"""按 SPEC.md §6 的 15 项校验逐条验 `语料元数据.xlsx`。跑完即删。

这是 SPEC 的验收脚本：SPEC 里写了什么，这里就查什么。任一项 FAIL 都说明表或 SPEC 有一方是错的。
"""
import collections
import os
import re
import sys

import openpyxl

# _kb 与本脚本同目录。在项目根跑 `python 脚本/_verify.py` 时脚本目录不在 sys.path 上，
# 补进去，这样在哪个目录调用都能跑。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kb import anchor_spans, read_text       # noqa: E402

# 本脚本在 脚本/ 下，项目根是上一级。表里 source_dir 是相对项目根的路径
# （「数据源\3.中国人民银行\有效文件」），故拼绝对路径时要带上 ROOT。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")


def src_path(row: dict) -> str:
    """把表里的 source_dir + file_name 拼成可打开的绝对路径。"""
    return os.path.join(ROOT, row["source_dir"], row["file_name"])


def anchors(scheme: str, text: str) -> list[str]:
    """按 SPEC §2.2 抽原文锚点，只取 clause_id 后缀。

    锚点逻辑在 _kb.py，与 _meta.py / pipeline.py 同一份。这是给 P1 的参考实现。
    """
    return [suffix for suffix, _, _ in anchor_spans(text, scheme)]

SPEC_ROWS, SPEC_COLS = 159, 19
POOLS = {"PBC": 75, "CSRC": 24, "GOV": 16, "NFRA": 16, "NPC": 12,
         "SPC": 7, "SSE": 5, "SZSE": 2, "CAC": 1, "BSE": 1}
SCHEMES = {"第X条": 119, "一、二、三": 32, "X.Y.Z": 8}
STATUSES = {"有效": 155, "尚未生效": 3, "已废止": 1}
NO_DOC_NO = 22

wb = openpyxl.load_workbook(XLSX)
ws = wb.active
hdr = [c.value for c in ws[1]]
rows = [dict(zip(hdr, r)) for r in ws.iter_rows(min_row=2, values_only=True)]

fails: list[str] = []


def check(no: int, name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {no:>2}. {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(f"#{no} {name} {detail}")


check(1, "行数", len(rows) == SPEC_ROWS, f"{len(rows)}")
check(2, "列数", len(hdr) == SPEC_COLS, f"{len(hdr)}")
check(3, "序号连续 1..159",
      [r["序号"] for r in rows] == list(range(1, SPEC_ROWS + 1)))

ids = [r["doc_id"] for r in rows]
dup = [k for k, v in collections.Counter(ids).items() if v > 1]
bad = [i for i in ids if not re.fullmatch(r"[A-Z]{2,5}-\d{3}", str(i))]
check(4, "doc_id 唯一且格式合规", not dup and not bad, f"重复{dup} 不合规{bad[:3]}")

pool = collections.Counter(str(i).split("-")[0] for i in ids)
check(5, "各机构池份数", dict(pool) == POOLS,
      "" if dict(pool) == POOLS else f"实际 {dict(pool)}")

missing = [f"{r['source_dir']}\\{r['file_name']}" for r in rows
           if not os.path.exists(src_path(r))]
check(6, "file_name 可打开", not missing, f"打不开 {len(missing)}: {missing[:2]}")

sch = collections.Counter(r["clause_scheme"] for r in rows)
check(7, "clause_scheme 分布", dict(sch) == SCHEMES and "无" not in sch,
      "" if dict(sch) == SCHEMES else f"实际 {dict(sch)}")
check(8, "clause_count 无 -1 / 无 0",
      all(isinstance(r["clause_count"], int) and r["clause_count"] >= 1 for r in rows),
      f"最小 {min(r['clause_count'] for r in rows)}")

blank = [r for r in rows if not r["doc_number"]]
# §5.1 的四类：法律 / 交易所自律规则 / 证券、期货投资咨询管理暂行办法 / 促进债券市场信用评级
OK_BLANK = ("中华人民共和国", "交易所", "证券、期货投资咨询管理暂行办法", "信用评级")
stray = [r["doc_title"][:34] for r in blank
         if not any(k in r["doc_title"] for k in OK_BLANK)
         and r["doc_type"] not in ("法律", "自律规则")]
check(9, "doc_no 空 = 22 且落在 §5.1 四类内",
      len(blank) == NO_DOC_NO and not stray,
      f"空 {len(blank)}；越界 {stray[:3]}")

st = collections.Counter(r["status"] for r in rows)
check(11, "status 分布", dict(st) == STATUSES, "" if dict(st) == STATUSES else f"实际 {dict(st)}")

wrong = [r["doc_id"] for r in rows
         for c in ("publish_date", "effective_date", "fetched_at")
         if r[c] is not None and not isinstance(r[c], int)]
check(12, "日期列全为 int", not wrong, f"非 int {wrong[:3]}")
check(13, "fetched_at 非空 = 159",
      sum(1 for r in rows if r["fetched_at"] is not None) == SPEC_ROWS)
check(14, "source_dir 以 数据源\\ 开头",
      all(str(r["source_dir"]).startswith("数据源") for r in rows))

# ---------- #10 沿革件 doc_no = 沿革行末位令号 ----------
# 例外：§5.2 的 MANUAL_DOC_NO 覆盖件。它们的 PDF 沿革行常没随最新修订更新。
LED = re.compile(r"令\s*第?\s*(\d{2,4})\s*号")
MANUAL = {
    "上市公司信息披露管理办法_中国证券监督管理委员会_证监会官网.pdf",
    "上市公司收购管理办法_中国证券监督管理委员会_证监会官网.pdf",
    "上市公司股权激励管理办法_中国证券监督管理委员会_证监会官网.pdf",
    "上市公司重大资产重组管理办法_中国证券监督管理委员会_中国政府网.pdf",
    "公司债券发行与交易管理办法_中国证券监督管理委员会_证监会官网.pdf",
    "存托凭证发行与交易管理办法（试行）_中国证券监督管理委员会_证监会官网.pdf",
    "证券基金经营机构董事、监事、高级管理人员及从业人员监督管理办法_中国证券监督管理委员会_证监会官网.pdf",
    "外商投资证券公司管理办法_中国证券监督管理委员会_证监会官网.pdf",
}
compared = mismatch = 0
for r in rows:
    if not re.search(r"令第?\d", str(r["doc_number"] or "")):
        continue
    if r["file_name"] in MANUAL:
        continue
    flat = re.sub(r"[\s　 ]+", "", read_text(src_path(r)))
    m = re.search(r"第[一1]条|第[一1]章", flat)
    hits = LED.findall(flat[: m.start() if m else 1500])
    if not hits:
        continue                      # 头部无沿革行（正文开篇即第一章），无从比对
    compared += 1
    if hits[-1] not in str(r["doc_number"]):
        mismatch += 1
print(f"[{'PASS' if not mismatch else 'FAIL'}] 10. 沿革件 doc_no = 末位令号  "
      f"可比对 {compared} 份，不符 {mismatch}")
if mismatch:
    fails.append("#10 沿革件 doc_no")

# ---------- #15 clause_id 契约可落地 ----------
total_a = collide = blank_a = count_off = 0
for r in rows:
    a = anchors(r["clause_scheme"], read_text(src_path(r)))
    total_a += len(a)
    if not a:
        blank_a += 1
    if len(set(a)) != len(a):
        collide += 1
    if len(a) != r["clause_count"]:
        count_off += 1
check(15, "clause_id 契约可落地",
      not collide and not blank_a and not count_off,
      f"{total_a} 锚点 / 撞车 {collide} / 空 {blank_a} / 与 clause_count 不符 {count_off}")

print()
if fails:
    print(f"### {len(fails)} 项未过：")
    for f in fails:
        print("   -", f)
else:
    print("### SPEC §6 全部 15 项校验通过")