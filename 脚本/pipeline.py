"""知识库流水线：解析 → 清洗 → 切分。

产出：
    产出/clean/{doc_id}.txt        清洗后的全文
    产出/chunks/{doc_id}.jsonl     一行一个条文 chunk

用法：
    python 脚本/pipeline.py --sample          # 抽 10 份样例（PDF×5 + DOCX×5）
    python 脚本/pipeline.py --doc PBC-049     # 指定若干份
    python 脚本/pipeline.py --all             # 全量 158 份

契约：SPEC.md §3（切分锚点）、技术方案 §4（8 条清洗规则）/ §5（分段）/ §5.4（上下文头）。
锚点逻辑在 _kb.py，与 _meta.py / _verify.py 同一份 —— 不许在这里另写一套。
"""
import argparse
import collections
import json
import os
import re
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kb import anchor_spans, cn2int, detect_scheme, read_pages  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "产出")
CLEAN_DIR = os.path.join(OUT, "clean")
CHUNK_DIR = os.path.join(OUT, "chunks")

# ---------- 切分阈值（技术方案 §5.3）----------
LONG_CHAR = 1000        # 超长条阈值
SHORT_CHAR = 30         # 超短条阈值
OVERLAP = 80            # 超长条二次切的重叠字数
FALLBACK_CHUNK = 400    # 无条款结构兜底：每片字数
FALLBACK_OVERLAP = 100

RE_CHAPTER = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+章[^\n]*)", re.M)
RE_SECTION = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+节[^\n]*)", re.M)

# ---------- 清洗规则的判据 ----------

# 规则 2：页码。纯数字，可带括号/破折号
RE_PAGENO = re.compile(r"^\s*[-—－]?\s*\d{1,4}\s*[-—－]?\s*$")
RE_PAGENO_PAREN = re.compile(r"^\s*[（(\[［]\s*\d{1,4}\s*[）)\]］]\s*$")

# 规则 3：行尾已是句末标点 → 不必与下行合并
SENT_END = "。；：！？!?;:…”』】》"
# 下一行是条款/章节起始 → 不能把它并上来
#
# X.Y.Z 这一支**不能要求数字后面有空白**。真实 PDF 里 `1.1` 常常独占一行
# （「第一章总则⏎1.1⏎为规范证券…」），加了空白要求就匹配不上，`1.1` 被并进
# 「第一章总则」，锚点当场消失。实测吃掉 SZSE-001 25 个、PBC-074 16 个。
# 判据要与 _kb.RE_BARE 一样宽 —— 凡 RE_BARE 认作锚点的行首，这里都得拦住。
RE_NEXT_ANCHOR = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百零〇\d]+[条章节]"
    r"|[（(][一二三四五六七八九十\d]+[）)]"
    r"|\d+(?:\.\d+)+(?![\.\d])"
    r"|[一二三四五六七八九十]+、)")

# 规则 4：只转这几类，中文标点（，；：？！、。）一律保留
_HALF_RANGES = (range(0xFF10, 0xFF1A),    # ０-９
                range(0xFF21, 0xFF3B),    # Ａ-Ｚ
                range(0xFF41, 0xFF5B))    # ａ-ｚ
_HALF_SINGLE = {0xFF08: "(", 0xFF09: ")", 0xFF05: "%"}

# 规则 5：条款号内多余空格
RE_CLAUSE_SP = re.compile(r"第\s*([一二三四五六七八九十百零〇\d]+)\s*([条章节])")

# 规则 6：零宽与方向控制字符
RE_ZERO_WIDTH = re.compile(r"[​-‏‪-‮﻿⁠]")

# 规则 9：gov.cn 公文属性表的标签。标签中间会夹空格（`索 引 号`）或 NBSP
# （NFRA-001 里是 `来\xa0\xa0源`），所以每个字之间都写成 `\s*`。
RE_META_LAB = re.compile(
    r"(?:索\s*引\s*号|主\s*题\s*分\s*类|发\s*文\s*机\s*关|成\s*文\s*日\s*期"
    r"|标\s*题|发\s*文\s*字\s*号|发\s*布\s*日\s*期|来\s*源|公\s*文\s*种\s*类"
    r"|主\s*题\s*词|有\s*效\s*性|废\s*止\s*日\s*期)\s*[：:]")

# 规则 8：繁简。只检测不强转 —— 转简要 opencc，为一个当前语料里可能不存在的情况
# 引依赖不划算。真检测到了再决定。
#
# 写成「繁体,简体」成对，同形的自动落选。之前直接手写繁体字集合，先后混进了
# 「案」「理」「行」三个简繁同形字（法律文本里满地都是），简体文件被报出两百多个
# "繁体字"。成对写法让这个错误结构上不可能发生。
_TRAD_PAIRS = [
    ("機", "机"), ("構", "构"), ("與", "与"), ("為", "为"), ("條", "条"),
    ("規", "规"), ("則", "则"), ("標", "标"), ("準", "准"), ("銀", "银"),
    ("險", "险"), ("證", "证"), ("從", "从"), ("業", "业"), ("員", "员"),
    ("國", "国"), ("會", "会"), ("發", "发"), ("佈", "布"), ("無", "无"),
    ("論", "论"), ("點", "点"), ("數", "数"), ("據", "据"), ("記", "记"),
    ("憶", "忆"), ("體", "体"), ("網", "网"), ("絡", "络"), ("資", "资"),
    ("訊", "讯"), ("檔", "档"), ("執", "执"), ("處", "处"), ("確", "确"),
    ("認", "认"), ("管", "管"), ("行", "行"), ("案", "案"), ("理", "理"),
]
TRAD_ONLY = frozenset(t for t, s in _TRAD_PAIRS if t != s)


def strip_running_heads(pages: list[str]) -> list[str]:
    """规则 1：剔掉在 >60% 的页里重复出现的行（页眉页脚）。

    页数 < 3 时跳过 —— 两页里重复一次说明不了任何事，反而有删掉真条文的风险。
    """
    if len(pages) < 3:
        return pages
    cnt: collections.Counter[str] = collections.Counter()
    for pg in pages:
        for line in {x.strip() for x in pg.split("\n") if x.strip()}:
            cnt[line] += 1
    thresh = len(pages) * 0.6
    # 长行不删：重复出现的长行更可能是真正文（如"商业银行应当…"），不是页眉
    bad = {ln for ln, c in cnt.items() if c > thresh and len(ln) < 60}
    if not bad:
        return pages
    return ["\n".join(x for x in pg.split("\n") if x.strip() not in bad) for pg in pages]


def half_width(s: str) -> str:
    """规则 4：全角数字/字母/括号/百分号转半角，中文标点保留。"""
    out = []
    for ch in s:
        o = ord(ch)
        if o in _HALF_SINGLE:
            out.append(_HALF_SINGLE[o])
        elif any(o in r for r in _HALF_RANGES):
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def merge_hard_wraps(lines: list[str]) -> list[str]:
    """规则 3：PDF 每行末尾都会硬换行，不合并会把一条条文拆成十几个短句。

    判据：当前行行尾不是句末标点，且下一行不以「第X条」「（一）」「1.2」开头 → 合并。
    """
    out: list[str] = []
    for ln in lines:
        if out:
            prev = out[-1]
            if (prev and prev[-1] not in SENT_END and not RE_NEXT_ANCHOR.match(ln)
                    and not RE_PAGENO.match(prev) and not RE_PAGENO_PAREN.match(prev)):
                out[-1] = prev + ln.strip()
                continue
        out.append(ln)
    return out


def strip_meta_block(text: str, min_lines: int = 3) -> tuple[str, int]:
    """规则 9：删掉 gov.cn 公文属性块（索 引 号 / 主 题 分 类 / 发 文 机 关 …）。

    gov.cn 网页把公文元数据渲染成一张表，另存成 PDF 后这张表被当正文读出来，
    标签单元格还按表格行数重复（`成文日期：` 连出 7 行才跟一个真值）。实测
    GOV-001/004/009、NFRA-001 四份中招，最后一条条文的尾巴上粘 360~450 字垃圾。

    **不能「从标记一刀切到文末」。** GOV-001 的属性块夹在正文中间，后面还接着
    第二十二、二十三条；NFRA-001 后面接着第三十五~三十七条 —— 切到末尾会连真
    条文一起删掉。所以按行取「连续 ≥min_lines 行的属性段」，只删这一段。

    NFRA-001 的属性块还与公文标题挤在同一行（`关于印发《…》四项配套制度的通知发文机关：`），
    所以每行**只删从该行第一个标签起的那一截**，行首的标题原地留下。

    已知边界：真条文若与属性值挤在同一行（`…成文日期：2018年04月02日第三十五条…`），
    这条会被误删。当前语料没这种排法，属性块后一律另起一行。
    """
    def is_meta(ln: str) -> bool:
        # 含标签、且不是一整句话 —— 真条文行几乎都带句末标点，属性行没有
        return bool(RE_META_LAB.search(ln)) and not any(ch in ln for ch in "。；！？")

    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, 0
    while i < len(lines):
        j = i
        while j < len(lines) and is_meta(lines[j]):
            j += 1
        if j - i >= min_lines:
            n += 1
            for ln in lines[i:j]:
                m = RE_META_LAB.search(ln)
                head = ln[: m.start()].strip() if m else ""
                if head:
                    out.append(head)
            i = j
        else:
            # 非属性行：**一次只前进一行**。写成 `out.extend(lines[i:j]); i = j`
            # 会死循环 —— 非属性行时 j == i，既不产出也不前进。
            out.append(lines[i])
            i += 1
    return "\n".join(out), n


def clean(pages: list[str]) -> tuple[str, dict]:
    """9 条清洗规则，**按序执行**（顺序会影响结果）。返回 (清洗后全文, 统计)。

    代码里的执行顺序是 1→6→2→3→4→5→9→7→8，两个例外都写在各自的位置上：
    6 要赶在其他正则做行首行尾判断之前，9 要赶在 7 压掉空行之前。
    """
    stat = collections.Counter()

    # 1. 去页眉页脚
    pages = strip_running_heads(pages)

    text = "\n".join(pages)

    # 6. 去零宽字符（放在前面，免得它挡住后面正则的行首行尾判断）
    text, n = RE_ZERO_WIDTH.subn("", text)
    stat["零宽字符"] = n

    lines = text.split("\n")

    # 2. 去页码
    kept = []
    for ln in lines:
        if RE_PAGENO.match(ln) or RE_PAGENO_PAREN.match(ln):
            stat["页码"] += 1
            continue
        kept.append(ln)
    lines = kept

    # 3. 硬换行合并
    before = len(lines)
    lines = merge_hard_wraps(lines)
    stat["合并换行"] = before - len(lines)

    text = "\n".join(lines)

    # 4. 全角转半角
    text = half_width(text)

    # 5. 条款号归一：第 十 条 → 第十条
    text, n = RE_CLAUSE_SP.subn(lambda m: f"第{m.group(1)}{m.group(2)}", text)
    stat["条款号归一"] = n

    # 9. 去公文属性块（编号 9、位置在 7 之前：它按行删，要赶在 7 压掉空行之前）
    text, n = strip_meta_block(text)
    stat["公文属性块"] = n

    # 7. 压缩空白：连续空白压成一个；空行去掉
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = "\n".join(x.rstrip() for x in text.split("\n")).strip()

    # 8. 繁简检测（不转）
    stat["疑似繁体字"] = sum(1 for ch in text if ch in TRAD_ONLY)

    return text, dict(stat)


# ---------- 切分 ----------


def body_end(text: str, start: int, end: int) -> int:
    """条文正文的终点：`[start, end)` 里若先出现章节标题，就切在标题之前。

    章节标题夹在两个条文锚点之间（`…第八条…⏎第二章 组织机构⏎第九条…`），只按
    下一个锚点收尾的话，标题会被算进上一条的正文：「第九条…」的尾巴上粘着
    「第二章 组织机构」。实测全库 **733 条**（9.2%）这样。凡是分章的文件全中招。

    标题文字丢掉不损失信息 —— 它本来就会被 `chapter_at()` 收进下一条的 `chapter`
    字段里。

    **行首还不够，还要两道守卫。** PDF 会把行内引用断开，让「第X节」落到行首，
    只看行首就会把句子当成节标题、把条文从中间剪断（`SZSE-002-5.2.17` 实测）：

    1. **上一行不能还在句中。** 与 `_kb.REF_TAIL` 同源：真标题前面是段末。
       注意「上一行为空」不算段末——PDF 常在断行处插空行（本例就是）。
    2. **这一行不能含句末标点。** 真标题是「第三节信息披露管理制度及监管」，
       假的是「第八节有关规定执行；收购人…」——一整句话。
    """
    cut = end
    for pat in (RE_CHAPTER, RE_SECTION):
        # 必须 finditer 逐个试：第一个「行首看起来像节标题」的往往是假货
        # （「第八节有关规定执行；…」），被守卫挡掉后要接着看下一个，
        # 不能因为第一个不合格就放弃整个正则 —— 真的标题就在它后面几行。
        for m in pat.finditer(text, start + 1, end):
            prev = text[:m.start()].split("\n")[-1].strip()
            if prev and prev[-1] not in SENT_END:        # 上一行还在句中 → 断行
                continue
            if any(ch in m.group(1) for ch in "。；！？"):  # 这一行是句子 → 不是标题
                continue
            cut = min(cut, m.start())
            break
    return cut


def chapter_at(text: str, pos: int) -> str | None:
    """pos 位置所属的章节路径，形如「第三章 资本充足率 第二节 资本充足率计算」。"""
    ch = None
    for m in RE_CHAPTER.finditer(text):
        if m.start() > pos:
            break
        ch = m.group(1).strip()
    sec = None
    for m in RE_SECTION.finditer(text):
        if m.start() > pos:
            break
        sec = m.group(1).strip()
    if ch and sec:
        return f"{ch} {sec}"
    return ch


def split_long(clause_id: str, body: str) -> list[tuple[str, str]]:
    """超长条二次切：固定滑窗，片间重叠 OVERLAP 字。

    技术方案 §5.3 建议「按款/项二次切」，但「款/项」的识别规则在长条文里不稳定，
    切错反而伤语义；固定滑窗更稳。片号记为 `{clause_id}-1`、`{clause_id}-2`。

    不超过 LONG_CHAR 的原样返回，不加后缀 —— 多数条走这条路。
    """
    if len(body) <= LONG_CHAR:
        return [(clause_id, body)]
    step = LONG_CHAR - OVERLAP
    parts: list[tuple[str, str]] = []
    for i, start in enumerate(range(0, len(body), step), 1):
        piece = body[start:start + LONG_CHAR]
        if piece.strip():
            parts.append((f"{clause_id}-{i}", piece))
        if start + LONG_CHAR >= len(body):
            break
    return parts


def split_fallback(text: str) -> list[str]:
    """无条款结构兜底：按自然段聚合到 ~400 字一片，重叠 100 字。

    当前 158 份全都落进了三种锚点体例（_verify.py 第 15 项「空 0」），所以这条路径
    在本语料上用不到。保留是因为规范性文件的自然增长会带进无编号文件。
    """
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > FALLBACK_CHUNK and buf:
            out.append(buf)
            buf = buf[-FALLBACK_OVERLAP:]
        buf += ("\n" if buf else "") + p
    if buf.strip():
        out.append(buf)
    return out or [text]


def split_doc(text: str, scheme: str, meta: dict) -> tuple[list[dict], int]:
    """按锚点切条，返回 (chunk 列表, 切出条文的锚点数)。

    第二个返回值是给闸门 2 用的：`clause_id` 对 `X.Y.Z` 体例形如 `SZSE-001-1.1`，
    本身含两个连字符，从外面的字符串去猜「有没有二次切后缀」会猜错（`-1.1` 会被
    当成后缀削掉）。所以基础条数只能在这里、在还知道 span 的时候数。
    """
    doc_id, title = meta["doc_id"], meta["doc_title"]
    spans = anchor_spans(text, scheme)

    if not spans:
        # 兜底
        pieces = split_fallback(text)
        return [{
            "clause_id": f"{doc_id}-F{i}", "doc_id": doc_id, "doc_title": title,
            "clause_no": "", "clause_index": i, "chapter": None,
            "text": piece, "embed_text": f"《{title}》\n{piece}",
            "is_short": len(piece) < SHORT_CHAR, "no_structure": True,
        } for i, piece in enumerate(pieces, 1)], len(pieces)

    chunks: list[dict] = []
    n_used = 0
    for idx, (suffix, anchor, start) in enumerate(spans):
        end = spans[idx + 1][2] if idx + 1 < len(spans) else len(text)
        body = text[start:body_end(text, start, end)]
        chapter = chapter_at(text, start)
        clause_no = re.sub(r"\s+", "", anchor)

        # 排序键：A53 → 53；其余体例用出现次序
        cindex = cn2int(suffix[1:]) if suffix.startswith("A") else idx + 1

        got = False
        for cid, piece in split_long(f"{doc_id}-{suffix}", body):
            piece = piece.strip()
            if not piece:
                continue
            got = True
            head = " ".join(x for x in (f"《{title}》", chapter, clause_no) if x)
            chunks.append({
                "clause_id": cid,
                "doc_id": doc_id,
                "doc_title": title,
                "clause_no": clause_no,
                "clause_index": cindex,
                "chapter": chapter,
                "text": piece,
                "embed_text": f"{head}\n{piece}",
                "is_short": len(piece) < SHORT_CHAR,
            })
        if got:
            n_used += 1
    return chunks, n_used


# ---------- 主流程 ----------


def load_meta() -> dict[str, dict]:
    """从 产出/语料元数据.xlsx 读每份文件的元数据。"""
    ws = openpyxl.load_workbook(os.path.join(OUT, "语料元数据.xlsx")).active
    hdr = [c.value for c in ws[1]]
    return {r[1]: dict(zip(hdr, r)) for r in ws.iter_rows(min_row=2, values_only=True)}


def src_path(row: dict) -> str:
    return os.path.join(ROOT, row["source_dir"], row["file_name"])


def run(rows: list[dict], dry: bool = False) -> dict:
    """跑一份份文件，返回汇总。

    **两道硬闸门，对不上就抛，不许静默通过**（_kb.py 开头那条「数出来一个数、
    切出来另一个数」的警告，就靠这里兑现）：

    1. 清洗后重测的体例/条数必须与 语料元数据.xlsx 里登记的**逐字相同**。
       清洗会改文本，一旦某条规则吃掉了行首锚点，这里立刻炸，而不是等到
       第 10 周发现知识库少了一批条文。
    2. 切出来的基础 clause_id 个数必须等于锚点数（超长条二次切只加 `-N` 后缀，
       不改变基础 id 的个数）。
    """
    os.makedirs(CLEAN_DIR, exist_ok=True)
    os.makedirs(CHUNK_DIR, exist_ok=True)
    total = 0
    summary = []
    gates: list[str] = []
    for row in rows:
        p = src_path(row)
        pages = read_pages(p)
        text, stat = clean(pages)
        scheme, n_expect = detect_scheme(text)
        chunks, n_used = split_doc(text, scheme, row)
        total += len(chunks)

        # 闸门 1：清洗后重测的体例/条数，与契约表登记的逐字相同
        if scheme != row["clause_scheme"] or n_expect != row["clause_count"]:
            gates.append(
                f"{row['doc_id']} 体例/条数与契约不符："
                f"清洗后 {scheme}/{n_expect}，契约 {row['clause_scheme']}/{row['clause_count']}")
        # 闸门 2：切出条文的锚点数 == 锚点数（超长条二次切只加后缀，不改这个数）
        if n_used != n_expect:
            gates.append(f"{row['doc_id']} 锚点 {n_expect} ≠ 切出条文的锚点 {n_used}")

        if not dry:
            with open(os.path.join(CLEAN_DIR, f"{row['doc_id']}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(text)
            with open(os.path.join(CHUNK_DIR, f"{row['doc_id']}.jsonl"), "w",
                      encoding="utf-8") as f:
                for c in chunks:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

        summary.append({
            "doc_id": row["doc_id"], "scheme": scheme,
            "锚点": n_expect, "chunk": len(chunks),
            "超长切成": sum(1 for c in chunks if c["clause_id"].count("-") > 2),
            "超短条": sum(1 for c in chunks if c["is_short"]),
            "繁体": stat.get("疑似繁体字", 0),
        })

    if gates:
        raise SystemExit(
            f"\n### 闸门未过，{len(gates)} 份对不上账 —— 产物不可信，已中止：\n"
            + "\n".join("   - " + g for g in gates))
    return {"total": total, "summary": summary}


def pick_sample(rows: list[dict], n: int = 10) -> list[dict]:
    """覆盖式抽样：每种「格式 × 体例」组合各取一份，再补极值。

    第一版是「前 5 份 PDF + 前 5 份 DOCX」，结果 10 份全是 `第X条`，
    `一、二、三` 和 `X.Y.Z`（全库 40 份）一份没覆盖 —— 而它们才是最可能切错的。
    抽样必须按组合覆盖，不能按文件顺序取前 N 个。
    """
    by: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        by[(r["file_name"].rsplit(".", 1)[-1].lower(), r["clause_scheme"])].append(r)

    picked: list[dict] = []
    seen: set[str] = set()
    for k in sorted(by):
        v = sorted(by[k], key=lambda r: r["clause_count"])
        r = v[len(v) // 2]                      # 取条数居中的，避开极端
        picked.append(r)
        seen.add(r["doc_id"])

    # 再补：全库条数最多与最少各一份，剩下按条数从大到小填满
    rest = sorted((r for r in rows if r["doc_id"] not in seen),
                  key=lambda r: -r["clause_count"])
    for r in ([rest[0], rest[-1]] if len(rest) >= 2 else []) + rest:
        if len(picked) >= n:
            break
        if r["doc_id"] not in seen:
            picked.append(r)
            seen.add(r["doc_id"])
    return picked[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="抽 10 份样例")
    ap.add_argument("--all", action="store_true", help="全量 158 份")
    ap.add_argument("--doc", nargs="*", default=[], help="指定 doc_id")
    ap.add_argument("--dry", action="store_true", help="只统计不落盘")
    args = ap.parse_args()

    meta = load_meta()
    rows = list(meta.values())

    if args.doc:
        rows = [meta[d] for d in args.doc]
    elif args.sample:
        rows = pick_sample(rows)
    elif not args.all:
        ap.error("要给 --sample / --all / --doc 之一")

    res = run(rows, dry=args.dry)

    print(f"{'doc_id':10} {'体例':10} {'锚点':>5} {'chunk':>6} {'超长切成':>8} {'超短':>5} {'繁体':>5}")
    for s in res["summary"]:
        print(f"{s['doc_id']:10} {s['scheme']:10} {s['锚点']:>5} {s['chunk']:>6} "
              f"{s['超长切成']:>8} {s['超短条']:>5} {s['繁体']:>5}")
    n_anchor = sum(s["锚点"] for s in res["summary"])
    print(f"\n{len(rows)} 份  |  锚点合计 {n_anchor}  chunk 合计 {res['total']}"
          f"  |  差值 {res['total'] - n_anchor:+d}（超长条二次切造成）")
    if not args.dry:
        print(f"产出：{CLEAN_DIR}\n      {CHUNK_DIR}")


if __name__ == "__main__":
    main()