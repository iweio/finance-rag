"""联合发文的 `doc_number` 只挂了最后一家机关的令号，补回全部联署机关。

原文首行本来就印着完整联署名单 + **一个共用令号**：

    中国人民银行 国家金融监督管理总局中国证券监督管理委员会令〔2025〕第11号

`_meta.py` 抽文号时正则只截到了紧邻「令」的那一家，于是 `PBC-026` 变成
「中国证券监督管理委员会令〔2025〕第11号」。全表 6 行，已记 契约/README.md §4 #8。

**这是照抄原文首行，不是编造。** 但抄之前必须证明「首行的机关 == org 列」——
原文机关名之间**没有分隔符**（`国家金融监督管理总局中国证券监督管理委员会`
切不开），所以反过来做：拿 `org` 列的顺序去**逐段吃掉**首行，吃不完全程就
说明首行还印了 org 列没记的机关（或写法我没见过），**直接不写**。

    python 脚本/_docnum_joint.py            # 只看要改成什么
    python 脚本/_docnum_joint.py --write    # 写回 xlsx
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")
CLEAN = os.path.join(ROOT, "产出", "clean")

# 复用 org 归一的映射（`_org_norm` 有 __main__ 守卫，import 它不会写表）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _org_norm import NORM, norm_org  # noqa: E402

MAX_BYTES = 128  # 对齐 脚本/ingest.py 的 doc_number max_length

# 首行里 `令〔…〕第…号` 之前的那一段就是联署名单
HEAD_PAT = re.compile(r"^(?P<blob>.*?)令〔\s*(?P<y>\d{4})\s*〕第\s*(?P<n>\d+)\s*号")
DN_PAT = re.compile(r"令〔\s*(?P<y>\d{4})\s*〕第\s*(?P<n>\d+)\s*号$")


def variants(part: str) -> list[str]:
    """一个全称的全部已知写法（全称本身 + NORM 里指向它的简称），长的先试。"""
    v = [part] + [k for k, full in NORM.items() if full == part]
    return sorted(v, key=len, reverse=True)


def consume(blob: str, parts: list[str]) -> bool:
    """用 parts 的**顺序**逐段吃掉 blob；吃得干干净净才算数。"""
    i = 0
    for p in parts:
        for c in variants(p):
            if blob.startswith(c, i):
                i += len(c)
                break
        else:
            return False
    return i == len(blob)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(XLSX)
    ws = wb.active
    cols = [c.value for c in ws[1]]
    ix = {c: n + 1 for n, c in enumerate(cols)}

    plans: list[tuple[int, str, str, str, str]] = []
    bad: list[str] = []
    for r in range(2, ws.max_row + 1):
        d = ws.cell(r, ix["doc_id"]).value
        dn = str(ws.cell(r, ix["doc_number"]).value or "")
        m = DN_PAT.search(dn)
        # 只看「令」字号：通知类的文号天然只有一家（银发〔Y〕N号），本就不该补
        if not m:
            continue
        parts = [t for t in norm_org(ws.cell(r, ix["org"]).value or "").split("、") if t]
        if len(parts) < 2:
            continue

        fp = os.path.join(CLEAN, f"{d}.txt")
        if not os.path.exists(fp):
            bad.append(f"{d}: 找不到 产出/clean/{d}.txt，无法核对首行")
            continue
        head = open(fp, encoding="utf-8").read(400)
        h = HEAD_PAT.match(head.replace(" ", "").replace("　", ""))
        if not h:
            bad.append(f"{d}: 首行里没有 `…令〔…〕第…号` —— 抽取逻辑要跟着改")
            continue
        blob = h["blob"]
        if not consume(blob, parts):
            bad.append(f"{d}: 首行联署名 `{blob}` 与 org 列 `{'、'.join(parts)}` 对不上 —— **不写**")
            continue
        if (h["y"], h["n"]) != (m["y"], m["n"]):
            bad.append(f"{d}: 首行令号〔{h['y']}〕第{h['n']}号 与 doc_number 的"
                       f"〔{m['y']}〕第{m['n']}号 不一致 —— **不写**")
            continue

        new = " ".join(parts) + f"令〔{h['y']}〕第{h['n']}号"
        # Milvus 的 doc_number 是 max_length=128，且**按 UTF-8 字节算**（不是字符）。
        # 补前缀会把值撑长：最长的一行（PBC-014，联署 4 家）到 113 字节，余量只有 15。
        # 超了 Milvus 直接拒插，报的还是看不懂的字段错，所以在这里挡住。
        nb = len(new.encode("utf-8"))
        if nb > MAX_BYTES:
            bad.append(f"{d}: 补全后 {nb} 字节 > {MAX_BYTES}（Milvus doc_number max_length）")
            continue
        if new != dn:
            plans.append((r, d, dn, new, blob))

    if bad:
        print(f"### 核对未过 {len(bad)} 条（**不写**）")
        for b in bad:
            print("  - " + b)
        sys.exit(1)

    print(f"待改 {len(plans)} 行（全表 令字号 + 联署 ≥2 家的就那么几行）\n")
    for _, d, old, new, blob in plans:
        print(f"  {d}")
        print(f"    首行  {blob}")
        print(f"    旧    {old}")
        print(f"    新    {new}")

    if not args.write:
        print("\n（dry-run，加 --write 才写回 xlsx）")
        return

    for r, d, old, new, blob in plans:
        ws.cell(r, ix["doc_number"]).value = new
        # 留痕：写清这个值是照哪一行抄的，不然下一个人不知道它为什么和别的不一样长
        tag = f"doc_number 补全联署机关（照抄原文首行：{blob}）"
        prev = ws.cell(r, ix["note"]).value or ""
        ws.cell(r, ix["note"]).value = (prev + "；" + tag) if prev and tag not in prev else (prev or tag)
    wb.save(XLSX)
    print(f"\n已补全 {len(plans)} 行 doc_number（含 note 留痕）")


if __name__ == "__main__":
    main()