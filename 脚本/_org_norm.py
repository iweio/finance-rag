"""把 `org` 列里的机关简称归一到全称（SPEC §2 第 5 列要求「发文机关全称」）。

按 `、` 拆开后原本有 **34 个取值**，同一个机关最多 4 种写法。不归一，
`/api/v1/meta/filters` 的 org 轴直接把列值当字典返回，**前端下拉里同一部门
会冒出好几个条目**，点哪个都只筛出一部分件。已记入 契约/README.md §4 #7。

校验项：

1. 归一后每个机关名必须落在**已知全称**集合里 —— 出现从没见过的名字就报错，
   不静默接受（可能是我漏配的简称）
2. **首个机关必须仍是 `_meta.AGENCY_CODE` 的键**。`doc_id` 的机构代码是按
   `first_agency(org)` 查这张表得来的，改坏了会让 `_meta.py` 重跑时 KeyError
3. `doc_id` 不能变 —— 归一的是 org，不是 id

**不 import `_meta`**：那会执行它 694 行模块级代码并重写 语料元数据.xlsx。
这里改成从源码文本里正则抽 `AGENCY_CODE` 的键。

    python 脚本/_org_norm.py            # 只看改动
    python 脚本/_org_norm.py --write    # 写回 xlsx，并生成 产出/org_归一表.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")
AUDIT = os.path.join(ROOT, "产出", "org_归一表.md")

# 简称 → 全称。只收**同一机关**的异写，不收前身/继任（银保监会 ≠ 金融监管总局，是两个机构）
NORM = {
    "中国证监会": "中国证券监督管理委员会",
    "证监会": "中国证券监督管理委员会",
    "国家外汇局": "国家外汇管理局",
    "金融监管总局": "国家金融监督管理总局",
    "银保监会": "中国银行保险监督管理委员会",
    "国家发展改革委": "国家发展和改革委员会",
    "发展改革委": "国家发展和改革委员会",
    # 联合发文里发改用过带国名的写法；官方全称不带「中华人民共和国」
    "中华人民共和国国家发展和改革委员会": "国家发展和改革委员会",
    "市场监管总局": "国家市场监督管理总局",
    # 中央网信办与国家互联网信息办公室是「一个机构两块牌子」，且
    # _meta.AGENCY_CODE 里 CAC 的键就是「国家互联网信息办公室」
    "中央网信办": "国家互联网信息办公室",
    "国家网信办": "国家互联网信息办公室",
}

# 已知全称（归一后允许出现的名字）。简称不在此列，出现即报错。
KNOWN = {
    "中国人民银行", "中国证券监督管理委员会", "国家金融监督管理总局",
    "中国银行保险监督管理委员会", "国务院", "全国人民代表大会及其常务委员会",
    "最高人民法院", "最高人民检察院", "国家外汇管理局", "财政部",
    "国家市场监督管理总局", "国家发展和改革委员会", "工业和信息化部",
    "公安部", "商务部", "民政部", "国家税务总局", "国家知识产权局",
    "国家互联网信息办公室", "国务院国有资产监督管理委员会",
    "上海证券交易所", "深圳证券交易所", "北京证券交易所",
}


def agency_codes_from_source() -> set[str]:
    """从 `_meta.py` 的源码文本里抠出 AGENCY_CODE 的键，不 import 它。"""
    src = open(os.path.join(ROOT, "脚本", "_meta.py"), encoding="utf-8").read()
    blk = re.search(r"AGENCY_CODE\s*=\s*\{(.*?)\n\}", src, re.S)
    if not blk:
        sys.exit("在 _meta.py 里找不到 AGENCY_CODE —— 抽取逻辑要跟着改")
    return set(re.findall(r'"([^"]+)"\s*:', blk.group(1)))


def norm_org(org: str) -> str:
    return "、".join(NORM.get(t.strip(), t.strip()) for t in re.split(r"、", org or "") if t.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    codes = agency_codes_from_source()
    wb = openpyxl.load_workbook(XLSX)
    ws = wb.active
    cols = [c.value for c in ws[1]]
    i_d, i_o = cols.index("doc_id") + 1, cols.index("org") + 1

    changes: list[tuple[str, str, str]] = []
    bad: list[str] = []
    for r in range(2, ws.max_row + 1):
        d, old = ws.cell(r, i_d).value, ws.cell(r, i_o).value or ""
        new = norm_org(old)
        for t in new.split("、"):
            if t not in KNOWN:
                bad.append(f"{d}: 归一后出现没见过的机关名 `{t}`（原名 `{old}`）")
        first = new.split("、")[0]
        if first not in codes:
            bad.append(f"{d}: 首个机关 `{first}` 不是 _meta.AGENCY_CODE 的键 —— 重跑 _meta.py 会 KeyError")
        if new != old:
            changes.append((d, old, new))

    if bad:
        print(f"### 校验未过 {len(bad)} 条（**不写**）")
        for b in bad:
            print("  - " + b)
        sys.exit(1)

    before = len({t for r in range(2, ws.max_row + 1)
                  for t in re.split(r"、", ws.cell(r, i_o).value or "") if t.strip()})
    after = len({t for r in range(2, ws.max_row + 1)
                 for t in re.split(r"、", norm_org(ws.cell(r, i_o).value)) })

    print(f"待改 {len(changes)} 行；机关取值 {before} → {after}")
    print(f"（首个机关校验：{len(codes)} 个 AGENCY_CODE 键全对上）\n")
    for d, o, n in changes:
        print(f"  {d}\n    旧 {o}\n    新 {n}")

    if not args.write:
        print("\n（dry-run，加 --write 才写回 xlsx）")
        return

    for r in range(2, ws.max_row + 1):
        ws.cell(r, i_o).value = norm_org(ws.cell(r, i_o).value)
    wb.save(XLSX)

    with open(AUDIT, "w", encoding="utf-8") as f:
        f.write("# org 归一表\n\n")
        f.write("SPEC §2 第 5 列要求「发文机关**全称**」，原列里有简称。归一的后果：\n")
        f.write(f"按 `、` 拆开的机关取值从 **{before} 个降到 {after} 个**，改了 {len(changes)} 行。\n\n")
        f.write("**只并异写，不并前身/继任**——`中国银行保险监督管理委员会`（2018-2023）与\n")
        f.write("`国家金融监督管理总局`（2023- ）是两个机构，各自保留。\n\n")
        f.write("## 映射\n\n| 简称 | 全称 |\n|---|---|\n")
        for k, v in NORM.items():
            f.write(f"| `{k}` | `{v}` |\n")
        f.write("\n> `中央网信办` / `国家网信办` → `国家互联网信息办公室`：一个机构两块牌子，\n")
        f.write("> 且 `_meta.AGENCY_CODE` 里 CAC 的键就是后者。\n\n")
        f.write("## 逐行\n\n| doc_id | 旧 | 新 |\n|---|---|---|\n")
        for d, o, n in changes:
            f.write(f"| `{d}` | {o} | {n} |\n")
    print(f"\n已归一 {len(changes)} 行，留痕写入 {os.path.relpath(AUDIT, ROOT)}")


if __name__ == "__main__":
    main()