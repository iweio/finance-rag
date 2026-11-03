"""从中国政府网官方页取《商业银行资本管理办法》正文，落成 数据源 里的一份 .docx。

    python 脚本/_fetch_nfra016.py            # 只看会生成什么
    python 脚本/_fetch_nfra016.py --write    # 落盘

**为什么是「转」而不是「下载原件」**
    该办法以「国家金融监督管理总局令2023年第4号」发布。gov.cn 的部门规章频道
    （/zhengce/202311/content_6913410.htm）只把**正文**内联在 HTML 里；页面挂的
    25 个 .docx/.doc 全是附件 1–25，**没有「办法正文」的单件原件**。
    所以本脚本把该页内联正文原样转成 .docx。
    这与其余 158 份（人工下载的原件）**不是同一类来源**，已在元数据 note 里写明。

**合规**
    - 只读 gov.cn。该站 robots.txt 是 `Allow: /1` + `Disallow: /2016*/`，
      本页路径 /zhengce/202311/... 不在禁止范围内（见 文档/数据来源合规说明.md）。
    - 不访问 pbc.gov.cn / flk.npc.gov.cn（两者 robots 是 `Disallow: /`）。
    - 单次请求，正常 User-Agent。

**取舍（三条都是刻意的，不是漏）**
    - 只取正文（第一条–第二百零六条），**25 个附件不纳入**：附件是分册的技术规则，
      附件 3 单份就顶得上正文；全收会把 1 份变成 26 份，而附录级文书在库里
      本来也只有 3 份。正文里对附件的援引（「（二十五）附件25：外部评级使用规范。」）
      保留原样，不删。
    - 页尾的「附件下载」清单（形如 `附件1：资本工具合格标准.docx`）是页面渲染块，
      不是正文，剔除。判据：`.docx`/`.doc`/`.pdf` 后缀结尾 —— 正文里的援引条目
      以「。」收尾，不会带后缀。
    - 正文中间那张「最低利润留存比例要求」表**就地摊平成文本行**，不做成 docx 表格：
      `_kb.read_pages` 读 docx 时把表格单元格追加在**全部段落之后**，做成真表格
      会让表格内容脱离它所在的那一条。
"""
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://www.gov.cn/zhengce/202311/content_6913410.htm"
CACHE = os.path.join(ROOT, "产出", "_fetch", "gov1.html")
# 文件名**故意不带「令2023年第4号」**。`_meta.py` 提文号的正则 `HW` 只认〔〕方括号体例
# （`[〔\[［]年份[〕\]］]第N号`），「令2023年第N号」这种**年份不带括号**的写法它不认，
# 会去猜、猜错就落一个错的 `doc_number`。文号本来就从沿革行提（本件沿革行里有完整的
# 「国家金融监督管理总局令2023年第4号公布」），文件名里再写一遍是多余的。
# 见 契约/README.md §4.1 第 ② 条。
OUT = os.path.join(ROOT, "数据源", "4.国家金融监督管理总局", "有效文件", "部门规章",
                   "商业银行资本管理办法_国家金融监督管理总局.docx")
UA = "Mozilla/5.0 (compatible; RAG-coursework/1.0)"
# 正文容器：页面里唯一的 pages_content 块
RE_BODY = re.compile(r'<div[^>]*class="[^"]*pages_content[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S)
RE_BLOCK = re.compile(r"<p[^>]*>(.*?)</p>|<table.*?</table>", re.S)
RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
RE_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# 页尾下载块：正文援引以「。」收尾，只有下载块带文件后缀
RE_DOWNLOAD_TAIL = re.compile(r"^附件\s*\d+\s*[:：].*\.(?:docx?|pdf|wps)$", re.I)


def strip_tags(s: str) -> str:
    """去标签并归一空白。`<br/>` 当空格，不当换行 —— 页面的 <br/> 是排版用的。"""
    s = re.sub(r"<br\s*/?>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def fetch() -> str:
    """取页面 HTML；已有缓存则直接用，不重复打 gov.cn。"""
    if os.path.exists(CACHE):
        return open(CACHE, encoding="utf-8", errors="replace").read()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    with open(CACHE, "w", encoding="utf-8") as f:
        f.write(raw)
    return raw


def body_paras(html: str) -> list[str]:
    """正文段落，按文档次序。表格就地摊平成 `\\t` 分隔的行。"""
    m = RE_BODY.search(html)
    if not m:
        raise SystemExit("页面结构变了：找不到 pages_content 容器，先看清楚再改")
    out: list[str] = []
    for b in RE_BLOCK.finditer(m.group(1)):
        if b.group(1) is None:                       # <table>
            for tr in RE_TR.finditer(b.group(0)):
                cells = [strip_tags(c) for c in RE_CELL.findall(tr.group(1))]
                line = "\t".join(c for c in cells if c)
                if line:
                    out.append(line)
            continue
        t = strip_tags(b.group(1))
        if t:
            out.append(t)
    while out and RE_DOWNLOAD_TAIL.match(out[-1]):   # 页尾下载块
        out.pop()
    return out


def main() -> None:
    paras = body_paras(fetch())
    heads = [p for p in paras if re.match(r"^第[一二三四五六七八九十百零〇]+条", p)]
    print("段落数", len(paras))
    print("行首「第X条」", len(heads), "首", heads[0][:12], "末", heads[-1][:14])
    print("沿革行", paras[1])
    print("末段", paras[-1][:80])
    if "--write" not in sys.argv:
        print("\n（未落盘。加 --write）")
        return
    import docx
    d = docx.Document()
    for p in paras:
        d.add_paragraph(p)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    d.save(OUT)
    print("\n已写", os.path.relpath(OUT, ROOT), os.path.getsize(OUT), "字节")


if __name__ == "__main__":
    main()