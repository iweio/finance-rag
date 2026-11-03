"""`publish_date` 记错的那些行，逐条按证据改正。契约/README.md §4 #9 / #10。

三种症状，对应三种错法：

| 症状 | 是什么 | 现值从哪来 |
|---|---|---|
| `施行日` | 把 `effective_date` 的值填进了 `publish_date`（两字段同值） | 正文的「自…起施行」 |
| `整月占位` | 正文只写「2026年4月修订」，被补成了「4月1日」 | 无，是补出来的 |
| `错件日期` | 现值在正文里找得到，但属于**被援引的另一份文件** | 别的文件 |

比如 `PBC-009` 记 20210101、`PBC-028` 记 20240101 —— 都是与 `effective_date` **同一个日子**，
而且都是元旦这种整日子。原文落款写着成文日：

    PBC-009  …中国银行保险监督管理委员会  2020年12月2日
    PBC-028  中国人民银行 国家金融监督管理总局  2023年10月7日

**依据分两类，闸门不同：**

- **本件正文能自证**（`src` 为 `None`）：脚本用 `find_evidence` 在**全文**里复现出新日期，
  复现不出来就不写。`PBC-009` / `028` / `GOV-006` / `PBC-065` / `071` / `SPC-004` 属此类。
- **只能靠外部官方页**（`src` 给了 URL）：本件正文里**根本没有**这个日期，复现不了。
  闸门换成「**现值必须能在本件正文里解释成施行日/占位值**」—— 防的是表被重新生成后
  我还在按旧值推理。`PBC-016` / `069` / `SSE-001` / `005` / `SZSE-002` 属此类。
  依据与 URL 一并写进 `note`，让后来的人能顺着它复核，而不是只能信 note 里那半句话。

**别用「头部出现了这个日期」当判据。** 试过全表扫描，`SPC-004` 就被误判成「命中」了——
它头部那个 `2009年2月28日` 是援引《刑法修正案(七)》的施行日，不是本文的公布日。
日期**在场**不等于**是本文的**，跟 §8.3 那个「官方页存在 ≠ 可填」是同一个坑。

    python 脚本/_pubdate_fix.py            # 只看要改成什么
    python 脚本/_pubdate_fix.py --write    # 写回 xlsx
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import openpyxl

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # 要打印正文片段，GBK 控制台下会崩

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "产出", "语料元数据.xlsx")
CLEAN = os.path.join(ROOT, "产出", "clean")

# doc_id: (现值, 改正值, 依据, 症状, 依据页 URL 或 None)
#
#   症状 `施行日`   —— 现值与 `effective_date` **相同**，即「把施行日填进了 publish_date」。
#                     改之前必须复现这个症状，否则说明表已经不是我认识的那张表。
#   症状 `整月占位` —— 同上（两字段同值），但正文里只有一个「年月」，没有日。
#   症状 `错件日期` —— 现值在本文正文里**找得到**，但属于**被援引的另一份文件**的日期。
#                     这一种浮不上来：`SPC-004` 的 20090228 是它援引的《保险法》修订日，
#                     看着像个正经日期，只有读到「2009年9月21日……公告公布」才知道填错了。
FIX = {
    # ---- 依据取自本件正文，脚本会复现一遍才写 ----
    "PBC-009": (20210101, 20201202, "落款「中国银行保险监督管理委员会 2020年12月2日」", "施行日", None),
    "PBC-028": (20240101, 20231007, "落款「中国人民银行 国家金融监督管理总局 2023年10月7日」", "施行日", None),
    # 2026-10-27 收录：查 URL 与残余缺陷时顺带发现的，同一症状（两个日期都被填成了施行日）
    "GOV-006": (19980401, 19971225, "正文头「1997年11月30日国务院批准 1997年12月25日国务院证券委员会发布」", "施行日", None),
    "PBC-065": (20210201, 20201118, "文末落款「中国人民银行2020年11月18日」（在附件清单之后，故头 800 字里根本没有）", "施行日", None),
    "PBC-071": (20040520, 20040412, "落款「行长：周小川 二○○四年四月十二日」（汉字数字，阿拉伯数字形态扫不到）", "施行日", None),
    "SPC-004": (20090228, 20090921, "公告「2009年9月21日最高人民法院公告公布」；现值 20090228 是本文所援引的《保险法》修订日", "错件日期", None),

    # ---- 2026-10-27 收录（同一症状，上一轮按外部查证清单改，漏了这 8 行）----
    # 起因是给生成器 _meta.dates_from_body 打补丁（沿革里同时印公布日和施行日时，
    # 「取头部最后一个日期」取到的是施行日）。补丁让这 8 行自动落到了正确值上，
    # 反过来说明它们本来错着。全部自证，不需要任何外部查证。
    # 沿革型的 5 份写法一致：「(YYYY年M月D日证监会令第N号公布 自YYYY年M月D日起施行)」
    "CSRC-002": (20080804, 20080603, "沿革「2008年6月3日证监会令第54号公布 自2008年8月4日起施行」", "施行日", None),
    "CSRC-011": (20140808, 20140707, "沿革「2014年7月7日证监会令第104号公布 自2014年8月8日起施行」", "施行日", None),
    "CSRC-012": (20201001, 20200828, "沿革「2020年8月28日证监会令第175号公布 自2020年10月1日起施行」", "施行日", None),
    "CSRC-013": (20140101, 20130924, "沿革「2013年9月24日证监会令第94号公布 自2014年1月1日起施行」", "施行日", None),
    "CSRC-014": (20150801, 20150626, "沿革「2015年6月26日证监会令第116号公布 自2015年8月1日起施行」", "施行日", None),
    # 人行令这三份的头部印的是**会议通过日**，不是公布日；公布日在文末汉字落款里。
    # 头部两个日期都不是公布日：PBC-036「经2005年6月16日第11次行长办公会议通过」是
    # 通过日、「自2005年10月1日起实施」是施行日 —— 现值取的正是后者（与同目录
    # PBC-071「经2004年3月22日…通过 / 落款二○○四年四月十二日」是同一形态）。
    "PBC-036": (20051001, 20050818, "落款「行长：周小川 二○○五年八月十八日」（汉字数字，阿拉伯数字形态扫不到）", "施行日", None),
    "PBC-037": (20070806, 20070703, "落款「行长：周小川 二○○七年七月三日」（汉字数字）", "施行日", None),
    # 司法解释的公告体：公告说「已于…通过，现予公布，自2019年7月1日起施行。」，
    # 公布日印在公告第二行「2019年6月27日」。头部后半段还整段援引了法释〔2019〕9号
    # （日期一模一样地重复一遍），所以「头部最后一个日期」怎么取都取不到 0627。
    "SPC-001": (20190701, 20190627, "公告第二行日期「2019年6月27日最高人民法院 最高人民检察院关于办理操纵证券、期货市场刑事案件适用法律若干问题的解释法释〔2019〕9号」", "施行日", None),

    # ---- 依据只能取自外部官方页（本件正文里没有这个日期）----
    "PBC-016": (20220806, 20210806,
                "官方页成文日期栏「2021年08月06日」＋正文落款「中国人民银行 国家发展改革委 财政部 银保监会 证监会 2021年8月6日」。"
                "现值 20220806 是正文「本通知自2022年8月6日起施行」的施行日 —— **只差一年，肉眼极易看漏**",
                "施行日", "https://www.gov.cn/zhengce/zhengceku/2021-08/08/content_5630150.htm"),
    "PBC-069": (20190101, 20180713,
                "官方页发布时间「2018-07-13 10:51:11」＋`meta PubDate`；现值 20190101 是正文第九条"
                "「应当于2019年1月1日起…提交大额交易报告」的施行日。"
                "**旁证（不靠外部即可判定现值是错的）**：本件文号是银发〔2018〕163号，而全表 89 份带年份文号的件里，"
                "只有这一份的 publish_date 年份与文号年份不符 —— 2019 这个年份本身就不成立",
                "施行日", "https://www.pbc.gov.cn/tiaofasi/144941/3581332/3730212/index.html"),
    "SSE-001": (20260401, 20260424,
                "官方页信息栏「发文文号 上证发〔2026〕44号　发布日期 2026年04月24日　生效日期 2026年04月24日　时效性 现行有效」"
                "＋落款「上海证券交易所 2026年4月24日」。现值 20260401 是把正文的「2026年4月修订」补成了当月 1 日",
                "整月占位", "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816605.shtml"),
    "SSE-005": (20260401, 20260424,
                "官方页信息栏「发文文号 上证发〔2026〕42号　发布日期 2026年04月24日　生效日期 2026年04月24日」"
                "＋落款「上海证券交易所 2026年4月24日」；规则正文页眉沿革以「2026年4月第二十次修订」收尾，与入库件同版",
                "整月占位", "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml"),
    "SZSE-002": (20260401, 20260424,
                "官方页「时间：2026-04-24」「深证上〔2026〕550号」＋落款「深圳证券交易所 2026年4月24日」；"
                "规则正文页眉沿革以「2026年4月第十一次修订」收尾，与入库件同版",
                "整月占位", "https://www.szse.cn/lawrules/rule/allrules/bussiness/t20260424_620189.html"),
}


DIG = "〇一二三四五六七八九"


def cn(d: int) -> str:
    """`2004年4月12日` —— 阿拉伯数字形态。"""
    return f"{d // 10000}年{d // 100 % 100}月{d % 100}日"


def _cn_num(n: int) -> str:
    if n < 10:
        return DIG[n]
    if n < 20:
        return "十" + (DIG[n - 10] if n > 10 else "")
    return DIG[n // 10] + "十" + (DIG[n % 10] if n % 10 else "")


def cn_loose(d: int) -> str:
    """`二○○四年四月十二日` —— 落款常用汉字数字（`PBC-071` 就是这种）。"""
    y = "".join(DIG[int(c)] for c in str(d // 10000))
    return f"{y}年{_cn_num(d // 100 % 100)}月{_cn_num(d % 100)}日"


def find_evidence(text: str, new: int) -> str | None:
    """在**全文**里找这个日期，返回它前后的一段上下文；找不到返回 None。

    判据两条，缺一不可：

    1. 日期以阿拉伯或汉字数字任一种形态原样出现。
       **只在头 800 字里找是不够的**：`PBC-065` 的落款在文末（附件清单之后）。
    2. 它**不是施行日** —— 「自…起施行」里的那个日期是 `effective_date` 的值，
       拿它填 `publish_date` 正是本脚本要修的错，闸门不能把它放过去。
    """
    flat = re.sub(r"[\s　]+", "", text).replace("〇", "○")
    for form in (cn(new), cn_loose(new).replace("〇", "○")):
        i = flat.find(form)
        while i != -1:
            before, after = flat[max(0, i - 8) : i], flat[i + len(form) : i + len(form) + 8]
            if not _is_shixing(before, after):
                return flat[max(0, i - 34) : i + len(form) + 6]
            i = flat.find(form, i + 1)
    return None


def _is_shixing(before: str, after: str) -> bool:
    """这个日期是不是被当**施行日**在用。「自2022年8月6日起施行」「于2019年1月1日起按照…」。

    判据放宽到「前一字是自/于/从」或「后一字就是起」：`PBC-069` 写的是
    「应当**于**2019年1月1日**起**按照本通知的规定」，既不是「自」也不是「起施行」，
    严格按 `自…起施行` 匹配会漏掉它，漏掉的表现是「症状对不上」而不是报错。
    """
    return before.endswith(("自", "于", "从")) or after.startswith("起")


def find_shixing(text: str, d: int) -> str | None:
    """反过来：在**全文**里找「本件把这个日期当施行日」的那处原文。

    只在「依据取自外部官方页」时用 —— 那时新日期复现不出来，能复现的只有**错**的那一半：
    现值确实在正文里当着施行日，说明它是从 `effective_date` 抄过来的，不是随手编的。
    """
    flat = re.sub(r"[\s　]+", "", text).replace("〇", "○")
    for form in (cn(d), cn_loose(d).replace("〇", "○")):
        i = flat.find(form)
        while i != -1:
            before, after = flat[max(0, i - 12) : i], flat[i + len(form) : i + len(form) + 10]
            if _is_shixing(before, after):
                return flat[max(0, i - 30) : i + len(form) + 8]
            i = flat.find(form, i + 1)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(XLSX)
    ws = wb.active
    cols = [c.value for c in ws[1]]
    ix = {c: n + 1 for n, c in enumerate(cols)}

    plans: list[dict] = []
    bad: list[str] = []
    done: list[str] = []
    for r in range(2, ws.max_row + 1):
        d = ws.cell(r, ix["doc_id"]).value
        if d not in FIX:
            continue
        old, new, why, sym, src = FIX[d]
        cur = ws.cell(r, ix["publish_date"]).value
        eff = ws.cell(r, ix["effective_date"]).value
        if cur == new:
            done.append(d)  # 本脚本上一轮已经改过，不重复计数、也不报错
            continue
        if cur != old:
            bad.append(f"{d}: 现值是 {cur}，与脚本记的 {old} 不符 —— 表已变，重新核过再改")
            continue
        # 两种「两字段同值」的症状都要求现值仍与 effective_date 相等：
        # 无论是「施行日被填进来」还是「把当月补成 1 日」，两个字段本来就是一齐错成同一个值的
        if sym in ("施行日", "整月占位") and cur != eff:
            bad.append(f"{d}: 现值 {cur} 与 effective_date（{eff}）不同，症状对不上 —— 先看清再改")
            continue
        if sym == "整月占位" and old % 100 != 1:
            bad.append(f"{d}: 现值 {old} 不是整月 1 日，「把当月补成 1 日」这个说法对不上 —— 先看清再改")
            continue

        text = open(os.path.join(CLEAN, f"{d}.txt"), encoding="utf-8").read()
        ctx = None
        if src is None:
            ctx = find_evidence(text, new)
            if not ctx:
                bad.append(f"{d}: 全文里找不到可当公布日的「{cn(new)}」/「{cn_loose(new)}」—— **不写**")
                continue
        elif sym == "施行日":
            # 外部依据复现不了，改为复现**错**的那一半：现值必须真在正文里当着施行日。
            # 复现得出，说明现值是从 effective_date 抄来的、不是随手编的，改它有据。
            ctx = find_shixing(text, old)
            if not ctx:
                bad.append(f"{d}: 现值 {old} 没有在正文里当施行日出现过，症状对不上 —— 先看清再改")
                continue
        # `整月占位` 的现值在正文里本来就找不到（正文只有「年月」），没什么可复现的
        plans.append({"r": r, "doc": d, "old": old, "new": new, "why": why,
                      "sym": sym, "src": src, "ctx": ctx})

    if bad:
        print(f"### 核对未过 {len(bad)} 条（**不写**）")
        for b in bad:
            print("  - " + b)
        sys.exit(1)

    print(f"待改 {len(plans)} 行\n")
    if done:
        print(f"（另外 {len(done)} 行已是改正值，跳过：{' '.join(done)}）\n")
    for p in plans:
        print(f"  {p['doc']}  {p['old']} → {p['new']}　[{p['sym']}]")
        print(f"      {p['why']}")
        if p["ctx"]:
            print(f"      本件原文…{p['ctx']}…")
        if p["src"]:
            print(f"      依据页 {p['src']}　★ 本件正文内无此日期，**不可自证**，写前人须看过该页")
        else:
            print("      依据取自本件正文，非外来查证")

    if not args.write:
        print("\n（dry-run，加 --write 才写回 xlsx）")
        return

    sym_desc = {"施行日": "原值误填施行日", "整月占位": "正文只有「年月」没有日，原值是把当月补成 1 日",
                "错件日期": "原值属本文所援引的另一份文件"}
    for p in plans:
        ws.cell(p["r"], ix["publish_date"]).value = p["new"]
        tag = f"publish_date 由 {p['old']} 改正为 {p['new']}：{sym_desc[p['sym']]}，{p['why']}"
        if p["src"]:
            tag += f"；依据页 {p['src']}（本件正文内无此日期，不可自证）"
        prev = ws.cell(p["r"], ix["note"]).value or ""
        ws.cell(p["r"], ix["note"]).value = (prev + "；" + tag) if prev and tag not in prev else (prev or tag)
    wb.save(XLSX)
    print(f"\n已改正 {len(plans)} 行 publish_date（含 note 留痕）")


if __name__ == "__main__":
    main()