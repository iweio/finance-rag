"""把并行子代理查回的 `source_url` 合并进 `产出/语料元数据.xlsx`。

六个代理各写一份 `产出/_url_results_*.json`，本脚本负责**核对后再落表**。
默认只做校验（dry-run），加 `--write` 才真写。

核这几件事：

1. **doc_id 必须在待查清单里** —— 代理可能把不在 158 份里的东西也查了
2. **不许有 doc_id 落在两份结果文件里** —— 后者会静默覆盖前者，先暴露再裁决
3. **URL 必须落在官方域名白名单内** —— 代理返回转载站就等于没查
4. **confidence 只能取三个值之一**，且 `none` 必须是空 URL、非 `none` 必须非空
5. **URL 必须能通过 `_verify.py` 的 source_url 列长度约束**（Milvus max_length=512 字节）

用法：

    python 脚本/_merge_urls.py            # 只看校验结果
    python 脚本/_merge_urls.py --write    # 校验全过才写
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")
TODO = os.path.join(ROOT, "产出", "_source_url_todo.json")

# 只认这些域名。代理返回别的就等于没查（契约/SPEC.md §8.3：宁可空不可编）
OK_HOSTS = (
    "pbc.gov.cn", "gov.cn", "npc.gov.cn", "nfra.gov.cn", "csrc.gov.cn",
    "court.gov.cn", "sse.com.cn", "szse.cn", "bse.cn", "cac.gov.cn",
    "safe.gov.cn", "chinamoney.com.cn", "nafmii.org.cn", "chinaclear.cn",
)
CONF = {"verified", "title-only", "none"}
MAX_BYTES = 512  # DB.md §3.1 的 source_url max_length，按 UTF-8 字节

# ★ 人工裁决：这些**不填**，即使代理给了 URL。
#
# 代理的 `confidence` 标签**不能直接照收** —— 同一份 `verified` 里混着两回事：
# 「页面上的件 == 我们入库的件」和「页面是这份件的**另一个版本**」。
# 后者指向的文本与库里的不同，用户照着 URL 去核会核不上，比留空更坏
# （同一个坑本轮踩到第三次：先是 gov.cn 副本差 3%~15%，再是这两批）。
# 判据统一是：**拿入库正文里的修正沿革/施行条款去比官方页的版本**。
HOLD = {
    "SPC-006": "court.gov.cn 页是 2013 年首发版；本库正文含「2020年12月23日审判委员会第1823次会议通过」，是 2020 修正版",
    "SPC-007": "官方页是 2018 年首发版；本库正文同样含 2020-12-23 修正记录，是 2020 修正版",
    "CSRC-003": "官方规章页沿革只到「2020年令第166号第五次修订」；本库是 2025-02-19 第227号令修订后版本",
    "CSRC-004": "官方页停在 2018 年令第148号修订；本库含 2025 修订",
    "CSRC-017": "官方页是 2018 年令第140号版；本库含 2025 修订",
    "CSRC-024": "官方页是 2022 年令第195号版；本库含 2025 修订",
    # 这一条是另一种形态：不是「官方页是旧版」，而是**根本找不到对应版本的页**。
    # 库里这份是 2019-09-30 第三次修订版，白名单域名上只有 2006 年令第478号原版。
    # 结论一样是留空 —— 「找不到」和「找到个不对的」都不能填。
    "GOV-010": "白名单域名上只有 2006-11-11 国务院令第478号原版；本库正文是 2019-09-30 第三次修订版（据《国务院关于修改〈中华人民共和国外资保险公司管理条例〉和〈中华人民共和国外资银行管理条例〉的决定》），无版本一致的合并版",
}


def host_ok(url: str) -> bool:
    """URL 的 host 是否落在官方白名单内（含子域）。"""
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].lower()
    return any(host == h or host.endswith("." + h) for h in OK_HOSTS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="校验全过后写回 xlsx")
    args = ap.parse_args()

    todo = {t["doc_id"] for t in json.load(open(TODO, encoding="utf-8"))}
    files = sorted(glob.glob(os.path.join(ROOT, "产出", "_url_results_*.json")))
    if not files:
        sys.exit("没有 产出/_url_results_*.json，子代理还没落盘")

    seen: dict[str, list[tuple[str, dict]]] = {}
    problems: list[str] = []
    for fp in files:
        tag = os.path.basename(fp).removeprefix("_url_results_").removesuffix(".json")
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            problems.append(f"{tag}: JSON 读不了 —— {e}")
            continue
        for it in data:
            d = it.get("doc_id", "?")
            if d not in todo:
                problems.append(f"{tag}: doc_id {d} 不在待查清单里（清单 {len(todo)} 份）")
                continue
            c, u = it.get("confidence"), (it.get("url") or "").strip()
            if c not in CONF:
                problems.append(f"{tag}/{d}: confidence 值非法 —— {c!r}")
            if c == "none" and u:
                problems.append(f"{tag}/{d}: 标了 none 却给了 URL")
            if c != "none" and not u:
                problems.append(f"{tag}/{d}: 标了 {c} 却没给 URL")
            if u and not host_ok(u):
                problems.append(f"{tag}/{d}: 非官方域名 —— {u[:70]}")
            if u and len(u.encode("utf-8")) > MAX_BYTES:
                problems.append(f"{tag}/{d}: URL 超 {MAX_BYTES} 字节（Milvus max_length）")
            seen.setdefault(d, []).append((tag, it))

    # 同一 doc_id 出现在多份结果里 —— 不静默取后者，全部列出来
    dup = {d: v for d, v in seen.items() if len(v) > 1}
    conflict = {d: v for d, v in dup.items() if len({i["url"] for _, i in v}) > 1}

    print(f"结果文件 {len(files)} 份，覆盖 doc_id {len(seen)}/{len(todo)}")
    for fp in files:
        print(f"  {os.path.basename(fp)}")

    if problems:
        print(f"\n### 校验未过 {len(problems)} 条（**不写**）")
        for p in problems:
            print("  - " + p)
        sys.exit(1)

    if dup:
        print(f"\n### 同一 doc_id 出现在多份结果里：{len(dup)} 个")
        for d, v in sorted(dup.items()):
            mark = "  ★ URL 还不一致，必须人工裁决" if d in conflict else ""
            print(f"  {d}{mark}")
            for tag, i in v:
                print(f"      [{tag}] {i['confidence']:10s} {i['url'][:80]}")
        if conflict:
            sys.exit(1)

    by_conf: dict[str, int] = {}
    for d, v in seen.items():
        by_conf[v[0][1]["confidence"]] = by_conf.get(v[0][1]["confidence"], 0) + 1
    print("\n" + "  ".join(f"{k} {v}" for k, v in sorted(by_conf.items())))

    missing = sorted(todo - set(seen))
    print(f"\n清单里还没结果的：{len(missing)} 份")
    if missing[:12]:
        print("  " + " ".join(missing[:12]) + (" …" if len(missing) > 12 else ""))

    # ★ 人工裁决在这里生效：代理给了 URL 也拒收。理由逐条打在下面，
    #   别让「HOLD 里有 6 个」变成一句没人知道为什么的话。
    held = {d: seen[d][0][1] for d in sorted(HOLD) if d in seen}
    if held:
        print(f"\n### 人工裁决不填（官方页版本与入库件不一致）：{len(held)} 份")
        for d, it in held.items():
            print(f"  [拒] {d}  代理标 {it['confidence']}，URL 丢弃")
            print(f"      {HOLD[d]}")

    fillable = {d: v[0][1] for d, v in seen.items()
                if v[0][1]["confidence"] != "none" and d not in HOLD}
    print(f"\n可落表（confidence != none 且不在 HOLD）：{len(fillable)} 份")

    if not args.write:
        print("\n（dry-run，加 --write 才写回 xlsx）")
        return

    wb = openpyxl.load_workbook(XLSX)
    ws = wb.active
    cols = [c.value for c in ws[1]]
    i_d, i_u, i_n = cols.index("doc_id") + 1, cols.index("source_url") + 1, cols.index("note") + 1
    n = m = 0
    for r in range(2, ws.max_row + 1):
        d = ws.cell(r, i_d).value
        old = ws.cell(r, i_n).value or ""
        if d in fillable:
            it = fillable[d]
            ws.cell(r, i_u).value = it["url"]
            # note 里留证据来源，别让后来人以为这行是原始采集带的
            src = {"verified": "标题+文号核对", "title-only": "仅标题+官方域名核对"}[it["confidence"]]
            tag = f"source_url 来源：联网查证（{src}）"
            ws.cell(r, i_n).value = (old + "；" + tag) if old and tag not in old else (old or tag)
            n += 1
        elif d in HOLD:
            # 留空要说明**为什么**空。否则下次谁再搜一轮，又会把这个旧版 URL 填回来。
            tag = "source_url 留空：官方页版本与入库件不一致（旧版 / 找不到对应版，详见 脚本/_merge_urls.py 的 HOLD）"
            ws.cell(r, i_n).value = (old + "；" + tag) if old and tag not in old else (old or tag)
            m += 1
    wb.save(XLSX)
    print(f"已写入 {n} 行 source_url（含 note 留痕）；{m} 行按 HOLD 留空并注明原因")


if __name__ == "__main__":
    main()