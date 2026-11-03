"""知识库流水线的公共底座：读文本 + 锚点。

`_meta.py`（生成元数据表）、`_verify.py`（校验）、`pipeline.py`（切分）三方共用这一份。
**不要再各写一份**：分开写的后果是「数出来一个数、切出来另一个数」，而且查不出是哪边错了。
本项目真踩到了：`_verify.py` 读原始文本数出 7830，`pipeline.py` 读清洗后文本切出 7789，
差的 41 个是清洗的硬换行合并规则吃掉的（`SZSE-001` 少 25、`PBC-074` 少 16）。
现在 `pipeline.py` 跑的时候会自己核对，对不上直接中止。

锚点规则对应 SPEC.md §3 / §3.6。
"""
import re

import docx
import fitz

# ---------- 读文本（SPEC §3.5）----------


def read_pages(p: str) -> list[str]:
    """按页读。PDF 一页一项；DOCX 无页概念，整篇算一页。

    清洗第 1 条「去页眉页脚」要按页统计重复行，所以这里保留页边界。

    **DOCX 的表格单元格必须去重。** python-docx 对合并单元格会在每个被合并的位置
    重复返回同一段文本：SSE-001 的表头「十一、承诺」横跨 18 行，被读成 18 个连续
    锚点、18 条 5 字"条文"。实测该文件 1321 次单元格读取里只有 235 次是真的，
    82% 是重复。

    （早前把这个坑记成「只读 paragraphs 会漏掉表格内容」——**是错的**。SSE-001
    实测：只读段落 162 个锚点，段落+表格朴素读 365 个，段落+表格去重 173 个。
    173 才是对的，365 是重复读撑出来的。表格确实要读，但不能朴素读。）
    """
    if p.lower().endswith(".pdf"):
        with fitz.open(p) as d:
            return [pg.get_text() for pg in d]
    d = docx.Document(p)
    parts = [x.text for x in d.paragraphs]
    prev = None
    for tb in d.tables:
        for row in tb.rows:
            for c in row.cells:
                if c.text == prev:          # 合并单元格的重复返回
                    continue
                parts.append(c.text)
                prev = c.text
    return ["\n".join(parts)]


def read_text(p: str) -> str:
    """PDF / DOCX 全文。

    PDF 分页必须 "\\n".join，用 "" 会把跨页两行粘成一行、吃掉行首锚点。
    """
    return "\n".join(read_pages(p))


# ---------- 锚点（SPEC §3）----------

CN = "一二三四五六七八九十百零〇"
RE_HEAD = re.compile(rf"^\s*第([{CN}\d]+)条", re.M)
RE_DUN = re.compile(r"^\s*([一二三四五六七八九十]+)、", re.M)
RE_BARE = re.compile(r"^\s*(\d+(?:\.\d+)+)(?![\.\d])\s*[\s　]?(?=\S)", re.M)

MIN_HEAD = 3        # 「第X条」最低命中数
MIN_DUN = 2         # 「一、」最低命中数
MIN_BARE = 3        # 「X.Y.Z」最低命中数

# 行首「第X条」的假锚点：PDF 会在「《期货交易管理条例》\n第八十二条」这种地方断行，
# 行首的条号其实是**援引别的法规**，不是本文条款。判据取最窄的一种——前一行的末尾
# 是书名号收尾。实测全库只影响 2 份，且逐一核对确认那两处都是援引：
#   CSRC-020 股票期权交易试点管理办法  第八十二条 → 援引《期货交易管理条例》
#   PBC-057 人行公告〔2026〕第25号       第三十一条 → 援引《征信机构管理办法》
# 更宽的判据（前一行非句末标点即剔除）会误杀 111/118 份的真条款，已弃用。见 SPEC §3.6。
REF_TAIL = re.compile(r"》\s*$")

_CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
             "九": 9, "零": 0, "〇": 0}


def cn2int(s: str) -> int:
    """中文数字转阿拉伯（SPEC §2.2）。条号量级 ≤ 千，够用。"""
    if s.isdigit():
        return int(s)
    total = num = 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in "十百千":
            total += (num or 1) * {"十": 10, "百": 100, "千": 1000}[ch]
            num = 0
    return total + num


def head_articles(text: str) -> set[str]:
    """行首「第X条」的条号集合（去重），剔掉跨行断句造成的援引假锚点。"""
    out: set[str] = set()
    for m in RE_HEAD.finditer(text):
        if REF_TAIL.search(text[: m.start()]):
            continue
        out.add(m.group(1))
    return out


def detect_scheme(text: str) -> tuple[str, int]:
    """返回 (clause_scheme, clause_count)。有序判定，先命中者胜。

    「第X条」必须过 MIN_HEAD 这一关才作数。没有门槛时，正文里孤零零一处行首
    「第X条」就能压过几百个「1.1」式条号：北交所、深交所创业板两份上市规则
    各有 1 处这样的行首命中，154 页整篇被当成 1 个切分单元。见 SPEC §3.4。
    """
    head = head_articles(text)
    if len(head) >= MIN_HEAD:
        return "第X条", len(head)
    dun = RE_DUN.findall(text)
    if len(dun) >= MIN_DUN:
        return "一、二、三", len(dun)
    bare = set(RE_BARE.findall(text))
    if len(bare) >= MIN_BARE:
        return "X.Y.Z", len(bare)
    return "无", 0


def anchor_spans(text: str, scheme: str) -> list[tuple[str, str, int]]:
    """给切分器用：按出现次序返回 [(clause_id 后缀, 锚点原文, 起始偏移)]。

    与 detect_scheme 同一套正则、同一套去重口径，保证「数的」和「切的」一致。
    偏移指向锚点起点，供调用方切片。
    """
    out: list[tuple[str, str, int]] = []
    if scheme == "第X条":
        seen: set[str] = set()
        for m in RE_HEAD.finditer(text):
            if REF_TAIL.search(text[: m.start()]):
                continue
            if m.group(1) in seen:      # 去重，与 head_articles 口径一致
                continue
            seen.add(m.group(1))
            out.append((f"A{cn2int(m.group(1))}", m.group(0).strip(), m.start()))
    elif scheme == "一、二、三":
        # 用出现次序编号，与 detect_scheme 的 len(dun) 口径一致
        for i, m in enumerate(RE_DUN.finditer(text), 1):
            out.append((f"S{i}", m.group(0).strip(), m.start()))
    elif scheme == "X.Y.Z":
        seen = set()
        for m in RE_BARE.finditer(text):
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
            out.append((m.group(1), m.group(1), m.start()))
    return out