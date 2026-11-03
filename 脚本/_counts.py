"""把契约文档里那几个「硬编码数字」重新数一遍，供人工同步用。不写任何文件。

    python 脚本/_counts.py

数的是：chunk 总数、锚点数、超长条二次切数、各字段空值数。
**每次动语料之后跑一次**，把结果抄进 契约/*.md —— 那几个数散在六份文档里，
手改容易漏，所以这里只负责报数，改还是人工改（改错了性质不同）。

锚点数用 `_kb.anchor_spans` 重读原文现算，与 `_verify.py` #15 同一份逻辑；
二次切数只能靠「chunk 数 − 锚点数」倒推 —— chunk 里没有标记哪片是二次切出来的，
`clause_id` 的 `-N` 尾缀也不可靠（`X.Y.Z` 体例的锚点自带点号数字，会误判）。
"""
import collections
import json
import os
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kb import anchor_spans, read_text       # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = os.path.join(ROOT, "产出", "chunks")
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")

n_chunk = 0
blank_chapter = 0
per_doc: collections.Counter[str] = collections.Counter()
for fn in sorted(os.listdir(CHUNKS)):
    if not fn.endswith(".jsonl"):
        continue
    with open(os.path.join(CHUNKS, fn), encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            n_chunk += 1
            per_doc[r["doc_id"]] += 1
            if not r.get("chapter"):
                blank_chapter += 1

wb = openpyxl.load_workbook(XLSX)
ws = wb.active
hdr = [c.value for c in ws[1]]
meta = [dict(zip(hdr, r)) for r in ws.iter_rows(min_row=2, values_only=True)]

# 锚点数：重读 159 份原文现算（要几十秒，这是唯一可信的口径）
n_anchor = 0
off: list[str] = []
for m in meta:
    p = os.path.join(ROOT, m["source_dir"], m["file_name"])
    n = len(anchor_spans(read_text(p), m["clause_scheme"]))
    n_anchor += n
    if per_doc.get(m["doc_id"], 0) != n and per_doc.get(m["doc_id"], 0) != n:
        off.append(f"{m['doc_id']} 锚点{n} chunk{per_doc.get(m['doc_id'], 0)}")

print("元数据行数 / 列数   ", len(meta), "/", len(hdr))
print("chunk 总数          ", n_chunk)
print("锚点数              ", n_anchor, "  ← 须等于 _verify.py #15 报的数")
print("超长条二次切        ", n_chunk - n_anchor, "  ← chunk − 锚点")
print("空 chapter          ", blank_chapter, "/", n_chunk)
print()
for col in ("publish_date", "effective_date", "expire_date", "source_url", "topic_tags"):
    n = sum(1 for m in meta if m[col] not in (None, ""))
    print(f"{col:<20}", f"{n} / {len(meta)}  = {n / len(meta):.1%}")
print("doc_number 空       ", sum(1 for m in meta if not m["doc_number"]))
print("doc_type 分布       ", dict(collections.Counter(m["doc_type"] for m in meta)))

# 条文行口径：有 source_url 的那批文件，它们名下的 chunk 数占全库多大比例。
# DB.md §3.1.3 用这个数与「文件口径」对照，说明「文件有 URL ≠ 条条有 URL」。
has_url = {m["doc_id"] for m in meta if m["source_url"]}
n_url_chunk = sum(v for k, v in per_doc.items() if k in has_url)
print("条文行有 URL 口径   ", n_url_chunk, "/", n_chunk, f"= {n_url_chunk / n_chunk:.1%}",
      "（无 URL 的", len(per_doc) - len(has_url & set(per_doc)), "份名下的行不算）")
print("domain 分布         ", dict(collections.Counter(m["domain"] for m in meta)))
print("clause_scheme 分布  ", dict(collections.Counter(m["clause_scheme"] for m in meta)))
print("status 分布         ", dict(collections.Counter(m["status"] for m in meta)))
print("机构池              ", dict(collections.Counter(str(m["doc_id"]).split("-")[0] for m in meta)))
if off:
    print("\n锚点 ≠ chunk 分的文件（差额即该文件的二次切数）：")
    for x in off:
        print("  ", x)