"""生成 158 份有效文件的元数据表（XLSX）。契约见 SPEC.md。

相对 v1 的三处修正：
1. clause_scheme —— 全库有 3 种切分锚点（第X条 / 一、二、三 / X.Y.Z），v1 只用
   「第X条」一种正则，把 38 份通知类和交易所规则算成 0 条。详见 SPEC.md §3。
2. clause_count 按行首条号去重 —— v1 全文匹配，把交叉引用也算成条款，实测虚高 3~18 条。
3. doc_no 从正文补 —— v1 只看文件名，截断的名字取不到文号；正文首行其实重复印了文号。

关键约定（沿用 v1）：
- domain 按【主题】判，判之前先剥掉发文机关前缀（否则「中国银行保险监督管理委员会」
  里的"保险"会抢先把银行类文件判成保险）。
- 3.中国人民银行 目录有两种形态：
    形态A 机关 令/公告〔Y〕第N号（规章名）   -> 标题=括号内，文号=令/公告号
    形态B 机关…关于X的通知（银发〔Y〕N号）  -> 标题=整串，文号=银发号
  判据：括号内容里含 〔Y〕第N号 就是文号，不是标题。
"""
import datetime
import os
import re
import sys

import openpyxl
from collections import Counter

# _kb 与本脚本同目录，补进 sys.path，在项目根调用也能跑。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kb import detect_scheme, read_text  # noqa: E402

# 本脚本在 脚本/ 下，项目根是它的上一级。__file__ 定位而非 cwd，
# 这样在哪个目录调用都能跑，且 source_dir 仍写成「数据源\...」（相对项目根），
# 与已发放的 158 行保持一致 —— 换写法会让 P1/P3 手里的路径全废。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "数据源")
OUT_DIR = os.path.join(ROOT, "产出")

HW = re.compile(r"[〔\[［]\s*(\d{4})\s*[〕\]］]\s*第?\s*(\d+)\s*号")
NOISE = re.compile(r"[（(][^（()）]*$")          # 结尾未闭合的括号片段

# 切分锚点（SPEC.md §3）与 read_text 都在 _kb.py，与 _verify.py / pipeline.py 共用一份。
# 这里只用 detect_scheme（数锚点）与 read_text（读文本）。

# 剥机关前缀用：机关名列表（长名在前，避免"中国银行"截断"中国银行保险监督管理委员会"）
ORGS = ["中国人民银行", "中国银行保险监督管理委员会", "国家金融监督管理总局",
        "中国证券监督管理委员会", "国家外汇管理局", "中华人民共和国国家发展和改革委员会",
        "国家发展和改革委员会", "国家发展改革委", "中华人民共和国财政部", "财政部",
        "中华人民共和国商务部", "商务部", "国务院国有资产监督管理委员会",
        "国家市场监督管理总局", "市场监管总局", "工业和信息化部", "公安部",
        "民政部", "国家税务总局", "中央网信办", "最高人民法院", "最高人民检察院",
        "国家知识产权局", "国家网信办", "住房城乡建设部", "中国证监会", "国家外汇局",
        "金融监管总局", "银保监会", "证监会", "发展改革委", "国资委"]
ORG_RE = re.compile("|".join(re.escape(o) for o in sorted(ORGS, key=len, reverse=True)))

# ---------- 主题归类：有序规则，先命中者胜。只对【已剥掉机关前缀】的标题匹配 ----------
DOMAIN_RULES = [
    ("外汇", ["外汇", "结售汇", "跨境人民币", "跨境税费", "离岸", "本外币", "境外贷款",
              "境外机构投资者境内", "境外机构境内发行债券", "资本项目", "外贸新业态",
              "稳外贸稳外资", "跨境"]),
    ("证券", ["证券", "期货", "基金", "上市公司", "股票", "债券", "交易所", "存托凭证",
              "股份转让", "并购重组", "股权激励", "重大资产重组", "私募", "投资咨询",
              "交易结算资金", "期权", "信用评级", "绿色债券", "买断式回购", "债券借贷",
              "标准化票据", "做市商", "融资券"]),
    ("保险", ["保险", "交强险", "投保", "保费"]),
    ("银行", ["银行", "储蓄", "存款", "贷款", "信贷", "支付", "清算", "备付金",
              "征信", "信用信息", "担保", "同业拆借", "金融控股", "资产管理公司",
              "理财", "人民币", "现金", "假币", "票据", "货币", "反洗钱",
              "反恐怖融资", "受益所有人", "金融消费者", "代收", "黄金", "国债",
              "供应链金融", "数据安全", "网络安全"]),
]
# 人工覆盖（规则判不准的）
OVERRIDE = {
    "存款保险条例": "银行",
    "中华人民共和国信托法": "综合",
    "中华人民共和国社会保险法": "综合",
    "中华人民共和国反洗钱法": "综合",
    "防范和处置非法集资条例": "综合",
    "国有重点金融机构监事会暂行条例": "综合",
    "金融机构撤销条例": "综合",
    "金融违法行为处罚办法": "综合",
    "网络安全审查办法": "综合",
    "中华人民共和国军人保险法": "保险",
    "金融基础设施监督管理办法": "综合",
    "标准化债权类资产认定规则": "综合",
    "信托公司开展房地产领域信托业务": "综合",
    "虚拟货币": "综合",
    "金融产品网络营销管理办法": "综合",
    "金融机构客户尽职调查": "综合",
    "受益所有人信息管理办法": "综合",
    "社会组织反恐怖融资管理办法": "综合",
    # 「资本债券」会先命中证券规则，但这件的发文对象是保险公司，归保险
    "保险公司发行无固定期限资本债券": "保险",
    "贵金属和宝石从业机构反洗钱": "综合",
    "中国人民银行规章制定程序与管理规定": "综合",
    "跨境税费缴库退库": "综合",
    "金融消费者权益保护实施办法": "综合",
    "个人金融信息保护技术规范": "综合",
    "绿色债券支持项目目录": "证券",
    "公司信用类债券信息披露管理办法": "证券",
    "信用评级业管理暂行办法": "证券",
    "促进债券市场信用评级行业健康发展": "证券",
    "全国银行间债券市场": "证券",
    "证券公司短期融资券管理办法": "证券",
    "银行间债券市场债券借贷业务管理办法": "证券",
    "完善银行间债券市场现券做市商": "证券",
    "修订银行间债券市场债券交易流通": "证券",
    "银行间外汇市场管理规定": "外汇",
    "规章制定程序": "综合",
    "假币收缴": "银行",
    "残缺污损人民币": "银行",
    "买卖人民币": "银行",
    "人民币现金收付": "银行",
    "中央银行存款账户": "银行",
    "人民币跨境支付系统": "综合",
    "黄金租借业务": "银行",
    "储蓄国债": "银行",
}

# 文件自己的文号，正文首行通常重复印一遍。取正文前若干字里的首个命中。
# 两族写法：
#   年号式 银发〔2021〕259号 / 金规〔2025〕24号 / 公告〔2025〕第21号 / 法释〔2003〕10号
#   序号式 国务院令第660号 / 中国人民银行令〔2004〕第1号 / 中国证券监督管理委员会令第35号
# 序号式的「第」可能有也可能没有：国务院令666号 与 证监会令第192号 并存同一份文件里。
NO_SEQ = re.compile(
    r"(?:中华人民共和国)?(国务院令|中国人民银行令|中国银行保险监督管理委员会令"
    r"|中国证券监督管理委员会令|中国证监会令|证监会令|银保监会令|银监会令|保监会令"
    r"|国家金融监督管理总局令|金融监管总局令)"
    # 年份三种写法，都要认：
    #   无年份  国务院令第660号 / 证监会令第54号
    #   〔Y〕    中国人民银行令〔2005〕第3号
    #   YYYY年  国家金融监督管理总局令2023年第4号  ← NFRA-016 起才有。
    #            银保监会/总局的令号**逐年重排**，年份不能丢，照抄沿革原文。
    r"(?:[〔\[［]\s*(\d{4})\s*[〕\]］]|(\d{4})\s*年)?\s*第?\s*(\d+)\s*号")
NO_YEARED = re.compile(
    r"(银办发|银发|银保监发|银监发|保监发|金规|法释|公告)"
    r"[〔\[［]\s*(\d{4})\s*[〕\]］]\s*第?\s*(\d+)\s*号")


def title_from_body(text: str) -> str:
    """令体例：本规章名以《X》出现在「《X》已经…会议审议通过」里。

    只能用这个严格句式。宽松的「取首个《X》」会把正文引用的其他法律法规
    （如《中华人民共和国中国人民银行法》）当成标题，等于编造。
    """
    m = re.search(r"《([^》]{4,60})》\s*(?:已经|已由|业经)", text[:2500])
    return m.group(1).strip() if m else ""


def head_region(text: str, cap: int = 1500) -> str:
    """正文头部、压掉空白后的字符串：从开头到「第一条/第一章」之前。

    修订沿革行只出现在这一段，限定范围是为了不让正文里的交叉引用
    （「根据《…》（银发〔2020〕226号）」）被当成本文件的文号。
    压空白是为了救 PDF 换行切断的机关名（「证\\n监会令第229号」）。
    """
    flat = re.sub(r"[\s　 ]+", "", text)
    m = re.search(r"第[一1]条|第[一1]章", flat)
    return flat[: min(m.start() if m else len(flat), cap)]


def doc_no_from_body(text: str) -> str:
    """从正文头部取本文件文号。找不到返回空串（不编造）。

    序号式取沿革行里的**最后一个**。沿革行形如
    「（1992年12月11日国务院令第107号发布 根据2011年1月8日国务院令第588号…修订）」，
    印发时的令号在前、现行版的在后。取第一个会把文件标成早已被修订掉的版本——
    语料里带沿革的 11 份全部会中招。
    """
    head = head_region(text)
    seqs = NO_SEQ.findall(head)
    if seqs:
        kind, y, n = seqs[-1][0], seqs[-1][1] or seqs[-1][2], seqs[-1][3]
        if not y:
            return f"{kind}第{n}号"
        # 〔Y〕写法保留括号；YYYY年写法照抄沿革原文，不改成〔Y〕——
        # 文号是对外引用串，不该由我们重排格式（与 §5.1「文号以正文为准」同一条原则）
        return (f"{kind}〔{y}〕第{n}号" if seqs[-1][1] else f"{kind}{y}年第{n}号")
    m = NO_YEARED.search(head)
    if m:
        kind, y, n = m.group(1), m.group(2), m.group(3)
        return f"公告〔{y}〕第{n}号" if kind == "公告" else f"{kind}〔{y}〕{n}号"
    return ""


def split_org_title(s: str) -> tuple[str, str]:
    """把「机关…标题」拆成 (机关串, 标题)。机关串由连续命中的机关名拼成。"""
    i, found = 0, []
    while True:
        m = ORG_RE.match(s, i)
        if not m:
            break
        found.append(m.group(0))
        i = m.end()
        while i < len(s) and s[i] in " 　、":
            i += 1
    return "、".join(dict.fromkeys(found)), s[i:].strip()


def doc_no_of(s: str) -> str:
    m = HW.search(s)
    if not m:
        return ""
    y, n = m.group(1), m.group(2)
    if "银办发" in s:
        return f"银办发〔{y}〕{n}号"
    if "银发" in s:
        return f"银发〔{y}〕{n}号"
    if "公告" in s:
        return f"公告〔{y}〕第{n}号"
    if "金规" in s:
        return f"金规〔{y}〕{n}号"
    if "银保监发" in s:
        return f"银保监发〔{y}〕{n}号"
    if "法释" in s:
        return f"法释〔{y}〕{n}号"
    return f"令〔{y}〕第{n}号"


# 「令〔Y〕第N号」「公告〔Y〕第N号」，尾部的「号」可能因文件名截断而缺失
DOCNO_LIKE = re.compile(r"^[一-龥]{0,8}?[〔\[［]\s*\d{4}\s*[〕\]］]\s*第?\s*\d*号?$")


def parse_dir3(stem: str, text: str) -> tuple[str, str, str, str]:
    """3.中国人民银行 目录：返回 (title, org, doc_no, note)

    三种形态：
      形态B 机关…关于X的通知（银发〔Y〕N号）        -> 括号里是文号，括号前整串是标题
      形态A 机关 令/公告〔Y〕第N号（规章名）        -> 文号后面的括号里是标题
      形态C 机关 公告〔Y〕第N号                     -> 公告无独立标题
    """
    note = ""
    # 形态B：找装着文号的那个括号组。不能只看第一个——标题里可能先出现别的括号，
    # 如「…关于印发 《储蓄国债（凭证式）管理办法》的通知（银发〔2021〕20号）」，
    # 第一个括号组是「（凭证式）」，看它会漏判。
    for tm in re.finditer(r"[（(]([^（()）]+)[）)]", stem):
        if HW.search(tm.group(1)):
            head = stem[: tm.start()].strip()
            org, _ = split_org_title(head)
            return head, org, doc_no_of(tm.group(1)), note
    # 形态A：文号之后的括号组里是标题（标题自身可能带括号，故锚定行尾）
    hwm = HW.search(stem)
    if hwm:
        m2 = re.match(r"\s*[（(](.+?)[）)]\s*$", stem[hwm.end():])
        if m2:
            org, _ = split_org_title(stem[: hwm.start()])
            return m2.group(1).strip(), org, doc_no_of(stem), note
    # 形态C / 其他：先剥掉截断的尾巴
    body = stem
    if NOISE.search(stem):
        body = NOISE.sub("", stem).strip()
        note = "文件名截断"
    m = re.search(r"关于.*?(?:通知|公告|决定|意见)$", body)
    if m:
        # 「机关…关于X的通知」：整串就是正式标题
        org, _ = split_org_title(body)
        return body, org, doc_no_of(stem), note
    org, rest = split_org_title(body)
    title = rest or body
    if not title or DOCNO_LIKE.match(title):
        # 公告类：正文里若印了规章名就用，否则退回法定称谓，不编造
        alt = title_from_body(text)
        if alt:
            return alt, org, doc_no_of(stem), note
        # 标题用空串占位，主循环会用最终确定的文号拼成法定称谓，
        # 避免这里拼出被截断的半截文号
        return "", org, doc_no_of(stem), \
            (note + " " if note else "") + "公告类无正式标题，以机关+文号代标题"
    return title, org, doc_no_of(stem), note


def parse_std(stem: str) -> tuple[str, str, str, str]:
    parts = [x.strip() for x in stem.split("_")]
    title, rest = parts[0], parts[1:]
    doc_no, orgs = "", []
    for x in rest:
        if HW.search(x) or re.match(r"^(JR|证监会公告)", x):
            doc_no = x
        elif x in ("证监会官网", "中国政府网", "国务院公报"):
            continue
        elif re.match(r"^\d{4}年", x):          # 版本标记，不入 org
            continue
        else:
            orgs.append(x)
    return title, "、".join(orgs), doc_no, ""


def doc_type_of(top: str, sub: str, title: str, doc_no: str) -> str:
    if top.startswith("1."):
        return "司法解释" if "最高人民法院" in title or "最高人民检察院" in title else "法律"
    if top.startswith("2."):
        return "行政法规"
    if top.startswith("3."):
        # 文号可能来自文件名（令〔Y〕第N号）或正文（中国人民银行令〔Y〕第N号），
        # 前缀不固定，只认里面有没有「令」
        return "部门规章" if "令" in doc_no else "规范性文件"
    if top.startswith("4."):
        if "法律行政法规" in sub:
            return "法律" if title.startswith("中华人民共和国") and title.endswith("法") else "行政法规"
        # 目录 4 原先只有「行政规范性文件」，`令`式部门规章无处可放，NFRA-016
        # （商业银行资本管理办法，总局令2023年第4号）起新开「部门规章」子目录。
        # 判据取子目录名而不是「doc_no 里有没有令」：本目录的 令 只有这一份，
        # 用子目录名更直白，也不会去动已有 15 份的`规范性文件`归类。
        return "部门规章" if "部门规章" in sub else "规范性文件"
    if top.startswith("5."):
        return "部门规章"
    if top.startswith("6."):
        return "自律规则"
    if top.startswith("7."):
        return "行业标准"
    return ""


# 开头的机关名可能连着一串（「中国人民银行 中国银行保险监督管理委员会 关于印发…」），
# 只剥一层的话第二个机关名会留下来——「中国银行保险监督管理委员会」里的"保险"
# 会把银行类文件判成保险（PBC-009、PBC-010 中过招）。所以要反复剥到剥不动为止。
AGENCY_PREFIX = re.compile(r"^(?:(?:%s)[、\s　]*)+" % ORG_RE.pattern)


def domain_of(raw_title: str) -> str:
    # 先剥掉机关前缀再判，避免机关名里的"保险""银行"污染
    body = AGENCY_PREFIX.sub("", raw_title)
    for k, v in OVERRIDE.items():
        if k in body:
            return v
    for dom, kws in DOMAIN_RULES:
        for kw in kws:
            if kw in body:
                return dom
    return "综合"


# 人工核定补录，键 = 磁盘文件名。只有解析器取不到或取错时才登记。
# 每条都得说得出出处；改动前先看 SPEC.md §5。
MANUAL_DOC_NO = {
    # 目录5：这 8 份是 2025 年修订版，PDF 头部没有沿革行（正文开篇即「第一章」），
    # 文号读不出来。值取自《语料时效核查报告》对这几份的逐份核对结果。
    "上市公司信息披露管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第226号",
    "上市公司收购管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第227号",
    "上市公司股权激励管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第227号",
    "上市公司重大资产重组管理办法_中国证券监督管理委员会_中国政府网.pdf": "证监会令第230号",
    "公司债券发行与交易管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第222号",
    "存托凭证发行与交易管理办法（试行）_中国证券监督管理委员会_证监会官网.pdf": "证监会令第215号",
    "证券基金经营机构董事、监事、高级管理人员及从业人员监督管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第227号",
    "网络安全审查办法_国家互联网信息办公室_中国政府网.pdf": "13部门令第8号",
    # 目录5：2020、2025 两次修正只写了《决定》名、没写令号，
    # 沿革行末位令号仍是 2018 年那个，推不出现行文号
    "外商投资证券公司管理办法_中国证券监督管理委员会_证监会官网.pdf": "证监会令第227号",
    # 目录1：司法解释用「法释」号，正文沿革行里不印
    "最高人民法院关于审理期货纠纷案件若干问题的规定_最高人民法院.docx": "法释〔2003〕10号",
    "最高人民法院关于审理期货纠纷案件若干问题的规定（二）_最高人民法院.docx": "法释〔2011〕1号",
    "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（二）_最高人民法院.docx": "法释〔2013〕14号",
    "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（三）_最高人民法院.docx": "法释〔2015〕21号",
    "最高人民法院关于适用《中华人民共和国保险法》若干问题的解释（四）_最高人民法院.docx": "法释〔2018〕13号",
    # 目录2：国务院令在括注里（「2021年10月26日国务院令第749号」），不在令头位置
    "证券期货行政执法当事人承诺制度实施办法.docx": "国务院令第749号",
    # 目录3：文件名被截断在「银发〔202」，且首页是附件目录、正文里也没有文号
    "中国人民银行 发展改革委 证监会关于印发《绿色债券支持项目目录（2021年版）》的通知（银发〔202.pdf": "银发〔2021〕96号",
    }

# 人工核定补录的完整标题。这三份的原文件名在 60 字处被截断，标题只剩前半截。
MANUAL_TITLE = {
    "中国人民银行 金融监管总局 最高人民法院 国家发展改革委 商务部 市场监管总局关于规范供应链金融业务.pdf":
        "中国人民银行 金融监管总局 最高人民法院 国家发展改革委 商务部 市场监管总局关于规范供应链金融业务"
        " 引导供应链信息服务机构更好服务中小企业融资有关事宜的通知",
    "中国人民银行 中央网信办 最高人民法院 最高人民检察院 工业和信息化部 公安部 市场监管总局 银保监.pdf":
        "中国人民银行 中央网信办 最高人民法院 最高人民检察院 工业和信息化部 公安部 市场监管总局 "
        "银保监会 证监会 外汇局关于进一步防范和处置虚拟货币交易炒作风险的通知",
    "中国人民银行 国家发展改革委 工业和信息化部 公安部 市场监管总局 金融监管总局 中国证监会 国家外.pdf":
        "中国人民银行 国家发展改革委 工业和信息化部 公安部 市场监管总局 金融监管总局 中国证监会 "
        "国家外汇局关于进一步防范和处置虚拟货币等相关风险的通知",
}

# 文件名里带的年号式文号不参与修订沿革，比正文可靠——
# 正文里出现的令号可能是它援引的别的法规。实例：《…融资担保公司监督管理条例
# 四项配套制度的通知》正文援引国务院令第683号，但它自己的文号是银保监发〔2018〕1号。
YEARED_IN_NAME = re.compile(r"(银办发|银发|银保监发|银监发|保监发|金规|法释|公告)"
                            r"[〔\[［]\s*\d{4}\s*[〕\]］]")

# 机构代码：doc_id 的第一段（SPEC §2.1）。键是 org 里的「首个机关」。
# 银保监会是金融监管总局的前身，并回 NFRA，不另立池；
# 网信办不在原机构表里，补 CAC。
AGENCY_CODE = {
    "中国人民银行": "PBC",
    "中国证券监督管理委员会": "CSRC",
    "国务院": "GOV",
    "国家金融监督管理总局": "NFRA",
    "中国银行保险监督管理委员会": "NFRA",
    "全国人民代表大会及其常务委员会": "NPC",
    "最高人民法院": "SPC",
    "上海证券交易所": "SSE",
    "深圳证券交易所": "SZSE",
    "北京证券交易所": "BSE",
    "国家互联网信息办公室": "CAC",
}

# 人工核定 status，键 = 磁盘文件名。只登记核查报告里有明确证据的行，
# 其余一律「有效」——不猜。取值见 SPEC §2 枚举。
MANUAL_STATUS = {
    # 《语料时效核查报告》「必须处理的 5 份」：附则写明施行日，且都在今天之后
    "国家金融监督管理总局关于印发《信托公司开展房地产领域信托业务管理办法(试行)》的通知_金规〔2026〕8号.pdf":
        "尚未生效",   # 第七十条：2027-03-01 起施行
    "国家金融监督管理总局关于印发《财产保险公司保险产品开发管理规定》的通知_金规〔2026〕9号.pdf":
        "尚未生效",   # 第四十一条：2026-11-01 起施行
    # 目录3：修改《企业征信机构备案管理办法》部分条款，2026-10-16 发布，2026-11-15 施行
    "中国人民银行公告〔2026〕第25号.pdf":
        "尚未生效",
    # 目录3：银发〔2021〕237号，已被同目录 银发〔2026〕42号（PBC-015）取代。
    # 文件留在 有效文件/ 不动 —— 移走会打乱目录遍历序，已发放的 doc_id 就废了。
    "中国人民银行 中央网信办 最高人民法院 最高人民检察院 工业和信息化部 公安部 市场监管总局 银保监.pdf":
        "已废止",
}

# 人工备注补录，键 = 磁盘文件名。登记「表里放不下、但检索时必须知道」的事。
MANUAL_NOTE = {
    # 这份规则 2026 年修订后大部分生效、5 处暂缓。暂缓清单是同一目录下的
    # 一份独立文件（附件3），单看正文读不出来，故在此标出。
    "上海证券交易所交易规则_上海证券交易所_2026年修订.docx":
        "部分条文暂缓实施：3.6.2(三)、3.6.3(二)(三)、3.6.4第二款、3.6.9第一款、3.6.10第五款，"
        "实施时间另行通知；清单见同目录《上海证券交易所交易规则（2026年修订）暂缓实施条文》",
    # 目录3：检索确认已被 银发〔2026〕42号 取代，但它仍在 有效文件/ 里（见 MANUAL_STATUS 说明）
    "中国人民银行 中央网信办 最高人民法院 最高人民检察院 工业和信息化部 公安部 市场监管总局 银保监.pdf":
        "已废止：被银发〔2026〕42号《关于进一步防范和处置虚拟货币等相关风险的通知》取代（同库 PBC-015）",
    # 目录3：2021 年版目录已被 2025 年版替代，正文需以新版为准
    "中国人民银行 发展改革委 证监会关于印发《绿色债券支持项目目录（2021年版）》的通知（银发〔202.pdf":
        "已被《绿色金融支持项目目录（2025年版）》（银发〔2025〕132号）替代，检索时以新版为准",
    # 目录3：银发〔2026〕171号废止了 9 件旧房地产信贷文件，322 号是否在名单内未能确认
    "中国人民银行 中国银行保险监督管理委员会关于建立银行业金融机构房地产贷款集中度管理制度的通知（银发〔.pdf":
        "存疑：可能已被银发〔2026〕171号（同库 PBC-029）列入废止清单，需对照该通知原文清单核实",
}

# 施行日按「法定默认」回填，键 = 磁盘文件名。值 = 该件的 publish_date（YYYYMMDD）。
#
# 依据：部门规范性文件未另定施行日的，自发布之日起施行——这是这类文件的**法定生效规则**，
# 填进去的就是真实生效日，不是编造。前置条件是 publish_date 必须已有值，否则无从推。
# 触发场景：这 5 份通知正文通篇不出现「施行」二字（实测 grep 命中 0），解析器无从取。
# 保留此表的理由与 SPEC §8.2 一致：effective_date 为 NULL 会让 Milvus 的
# `effective_date <= today` 过滤**静默漏掉**这些文件——不报错，只是查询结果少几条。
MANUAL_EFF = {
    # 人行 银发〔2018〕163号，正文无施行句，落款 2018-12-29
    "中国人民银行关于非银行支付机构开展大额交易报告工作有关要求的通知（银发〔2018〕163号）.pdf":
        "法定默认：通知类未另定施行日，自发布之日起施行",
    # 金规〔2025〕24号，正文无施行句
    "国家金融监督管理总局关于调整保险公司相关业务风险因子的通知_金规〔2025〕24号.pdf":
        "法定默认：通知类未另定施行日，自发布之日起施行",
    # 金规〔2025〕17 / 22 / 26 号，三份「废止和宣布失效的通知」，正文无施行句
    "国家金融监督管理总局关于部分规范性文件废止和宣布失效的通知_金规〔2025〕17号.pdf":
        "法定默认：通知类未另定施行日，自发布之日起施行",
    "国家金融监督管理总局关于部分规范性文件废止和宣布失效的通知_金规〔2025〕22号.pdf":
        "法定默认：通知类未另定施行日，自发布之日起施行",
    "国家金融监督管理总局关于部分规范性文件废止和宣布失效的通知_金规〔2025〕26号.pdf":
        "法定默认：通知类未另定施行日，自发布之日起施行",
}


# ---------- source_url：只取文件自身印着的官方链接 ----------
# 打印网页时页脚会带上原文 URL，25 份文件里有。这是**抽取**不是猜。
# 但一份文件里出现多个 URL 时（NFRA 有 5 份同时印了正文和「答记者问」），
# 分不清哪个是它自己——取第一个就是猜，故一律留空。宁可空，不可填错。
URL_RE = re.compile(r"https?://[^\s一-鿿\)）\",，、。；;·]+")
OFFICIAL_URL = re.compile(r"gov\.cn|sse\.com|szse\.cn|bse\.cn")


def clean_url(u: str) -> str:
    """清掉 URL 尾部粘连的页码（`…content_5724573.htm1/13` → `…content_5724573.htm`）。

    `.html` 后面跟 `?`/`&` 时不能截——那是查询串，正是 NFRA 链接的标识部分。
    """
    m = re.match(r"(.*?\.html?)(.*)$", u)
    if m and not m.group(2).startswith(("?", "&")):
        return m.group(1)
    return u.rstrip("·.,;")


def source_url_of(text: str) -> str | None:
    """文件自身印着的官方 URL；候选不是恰好一个时返回 None。"""
    flat = re.sub(r"\s+", "", text)
    cands = []
    for u in URL_RE.findall(flat):
        u = clean_url(u)
        if OFFICIAL_URL.search(u) and u not in cands:
            cands.append(u)
    return cands[0] if len(cands) == 1 else None


def first_agency(org: str) -> str:
    """取 org 串里的首个机关——doc_id 的机构代码按它定。"""
    return re.split(r"[、,]", org or "")[0].strip()


# ---------- 日期（SPEC §2，存 YYYYMMDD 整数）----------
DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 交易所规则的修订沿革只印到「2026年4月第二十次修订」，没有「日」
DATE_YM = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d+\s*日)")
# 附则里的施行语。允许「自…起施行 / 实施 / 生效」
# 「起」可有可无：PBC-070 写的是「本办法自2023年1月9日施行」，没有「起」字。
# 动词不止「施行/实施/生效」：PBC-033 写的是「自2024年2月18日起执行」。
EFF_DATED = re.compile(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:起)?\s*(?:施行|实施|生效|执行)")
# 「自」也可有可无：CSRC-008 写的是「本办法公布之日起施行」。
EFF_NOW = re.compile(r"(?:自\s*)?(?:发布|印发|公布|施行)之日(?:起)?\s*(?:施行|实施|生效|执行)")
# 行业标准（JR/T 系列）不写「自…起施行」，封面印的是「2020 - 02 - 13 发布」。
# 这是唯一一处阿拉伯数字日期格式，只在封面出现，故单独匹配。命中 PBC-074 / PBC-075。
# 连字符两侧**有空格**（实测 `2020 - 02 - 13 发布`），别写成紧贴的 `\d{4}-\d{2}-\d{2}`。
ISO_DATE = re.compile(r"(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*发布")
ISO_EFF = re.compile(r"(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*实施")

# 落款日期的排除条件。真落款日期在文末**独立成行**（机关署名之后），
# 而句子里的日期都带标记，两类会把日期取错：
#   前置「自」        → 「自2022年1月1日起施行」      这是**施行日**，不是发布日（GOV-008）
#   后接「发布/公布/印发」→ 「2021年3月18日发布的《…》同时废止」
#                      这是**被它废止的旧版**的日期（CSRC-001 取到 20210318、CAC-001 取到 20200413）
CITED_BEFORE = re.compile(r"自\s*$")
CITED_AFTER = re.compile(r"^\s*(?:发布|公布|印发)")
TAIL_LEN = 800

# 头部区间里「这个日期是施行日」的判据。与 CITED_BEFORE 同源，但文末只排前置「自」，
# 这里还要认后置的「起施行」—— 沿革行两处标记都有。
# 沿革行会**同时印公布日和施行日**：
#   （2023年10月26日国家金融监督管理总局令2023年第4号公布 自2024年1月1日起施行）
# 不剔的话「取头部最后一个日期」取到的就是施行日。库里 CSRC-002/011/012/013/014/016
# 六份正是这么错的（公布日在前、施行日在后），README §4 #9 那轮按外部查证清单改，
# 漏掉了这六份 —— 这六份的证据在沿革里自带，根本不需要外部查。
HEAD_EFF_BEFORE = re.compile(r"(?:自|于|从)\s*$")
HEAD_EFF_AFTER = re.compile(r"^\s*(?:起|施行|实施|生效|执行)")


def _is_effective_at(head: str, m: "re.Match[str]") -> bool:
    """这个日期是不是被当**施行日**在用。"""
    return bool(HEAD_EFF_BEFORE.search(head[: m.start()])
                or HEAD_EFF_AFTER.match(head[m.end():]))


def _ymd(y: str, m: str, d: str) -> int:
    return int(y) * 10000 + int(m) * 100 + int(d)


def tail_sign_date(text: str) -> int | None:
    """文末机关署名后的落款日期；取不到返回 None。

    通知类文件的发布日期只在文末落款处（正文开头是主送机关，没有日期），
    头部区间取不到，必须回文末找。排除条件见 CITED_BEFORE / CITED_AFTER。
    """
    flat = re.sub(r"\s+", "", text)
    tail = flat[-TAIL_LEN:]
    for m in reversed(list(DATE_CN.finditer(tail))):
        if CITED_BEFORE.search(tail[: m.start()]):
            continue
        if CITED_AFTER.search(tail[m.end():]):
            continue
        return _ymd(*m.groups())
    return None


def dates_from_body(text: str) -> tuple[int | None, int | None, str]:
    """返回 (publish_date, effective_date, note)，取不到给 None——不编造。

    publish_date 取**头部区间里剔掉施行日之后的最后一个日期**（判据见 _is_effective_at）：
    令头文件的最后一个日期是公布日，
    带沿革行的文件是现行版那一次修订的日期，两者都是「这一版是哪天出的」。
    交易所规则的沿革行只印到月（「2026年4月第二十次修订」），退回月精度记 1 号，
    并在 note 里写明——不能让人以为那天真是一号。

    头部取不到就回文末找落款日期（tail_sign_date）：通知类正文开头是主送机关，
    发布日期只在文末署名之后，如 PBC-053 / PBC-059 / PBC-068 一批。

    effective_date 取全文最后一个「自…年起施行」，附则在正文末尾，最后一条才是
    这份文件自己的施行日（前面出现的多是它援引的别的法规）。
    """
    head = head_region(text, cap=4000)
    note = ""
    pub = None
    hits = [m for m in DATE_CN.finditer(head) if not _is_effective_at(head, m)]
    if hits:
        pub = _ymd(*hits[-1].groups())
    else:
        ym = DATE_YM.findall(head)
        if ym:
            y, m = ym[-1]
            pub = int(y) * 10000 + int(m) * 100 + 1
            note = "publish_date 精度到月(原件只印年月)"

    eff = None
    dated = EFF_DATED.findall(text)
    if dated:
        eff = _ymd(*dated[-1])

    if pub is None:
        m = ISO_DATE.search(text)          # 行业标准封面：2020-02-13发布
        if m:
            pub = _ymd(*m.groups())
            note = "publish_date 取标准封面「发布」日期"
        else:
            pub = tail_sign_date(text)     # 通知类：文末落款
            if pub:
                note = "publish_date 取文末落款日期"

    if eff is None:
        m = ISO_EFF.search(text)           # 行业标准封面：2020-02-13实施
        if m:
            eff = _ymd(*m.groups())
        elif pub and EFF_NOW.search(text):
            # 「自发布/印发之日起施行」——施行日就是公布日
            eff = pub
    return pub, eff, note


DEFAULT_ORG = {"1.": "全国人民代表大会及其常务委员会", "2.": "国务院",
               "3.": "中国人民银行", "4.": "国家金融监督管理总局",
               "5.": "中国证券监督管理委员会", "6.": "证券交易所", "7.": "中国人民银行"}

rows = []
seq_by_code = Counter()   # doc_id 的池内流水号，按目录遍历顺序发，发完即冻结
for top in sorted(os.listdir(DATA)):
    d0 = os.path.join(DATA, top)
    if not os.path.isdir(d0) or not top[0].isdigit() or top.startswith("8"):
        continue
    for dp, _, fns in os.walk(d0):
        if "有效文件" not in dp:
            continue
        sub = os.path.relpath(dp, os.path.join(DATA, top))
        for f in sorted(fns):
            p = os.path.join(dp, f)
            stem = os.path.splitext(f)[0]
            try:
                text = read_text(p)
            except Exception as e:
                org0 = DEFAULT_ORG.get(top[:2], "")
                code = AGENCY_CODE.get(first_agency(org0), "UNK")
                seq_by_code[code] += 1
                rows.append([f"{code}-{seq_by_code[code]:03d}", f, stem, org0, "",
                             doc_type_of(top, sub, stem, ""), domain_of(stem), "有效",
                             None, None, None, -1, "无",
                             None, None, None, os.path.relpath(dp, ROOT),
                             f"读取失败:{e}"])
                continue
            if top.startswith("3."):
                title, org, doc_no, note = parse_dir3(stem, text)
            else:
                title, org, doc_no, note = parse_std(stem)
            if top.startswith("1.") and re.search(r"最高(人民)?(法院|检察院)", stem):
                org = "最高人民法院"

            # 文号：正文优先于文件名（文件名可能截断，也可能嵌了别人的文号）
            body_no = doc_no_from_body(text)
            if body_no and body_no != doc_no and not YEARED_IN_NAME.search(doc_no):
                if doc_no:
                    note = (note + " " if note else "") + f"文号以正文为准(文件名:{doc_no})"
                doc_no = body_no
            # 人工核定值最后落，盖过上面两条自动规则
            if f in MANUAL_TITLE:
                title = MANUAL_TITLE[f]
                note = (note + " " if note else "") + "标题人工补全(原文件名截断)"
            if f in MANUAL_DOC_NO:
                if doc_no and doc_no != MANUAL_DOC_NO[f]:
                    note = (note + " " if note else "") + f"文号人工核定(解析得:{doc_no})"
                doc_no = MANUAL_DOC_NO[f]
            dt = doc_type_of(top, sub, title, doc_no)
            if top.startswith("4.") and "法律行政法规" in sub:
                # 该目录横跨法律与行政法规两类，发文机关不能沿用目录名的
                # 「国家金融监督管理总局」——法律出全国人大，行政法规出国务院
                org = "全国人民代表大会及其常务委员会" if dt == "法律" else "国务院"
            if not org:
                org = DEFAULT_ORG.get(top[:2], "")
            if not title and doc_no:
                title = f"{org} {doc_no}".strip()

            # 人工核定的 status / note 最后落（同上，盖过默认值）
            status = MANUAL_STATUS.get(f, "有效")
            if f in MANUAL_NOTE:
                note = (note + " " if note else "") + MANUAL_NOTE[f]

            scheme, n = detect_scheme(text)
            dom = domain_of(title)
            if dom == "综合":
                # 标题信息不足（截断名/公告号代标题）时，用正文前 200 字再判一次
                dom = domain_of(title + " " + re.sub(r"\s+", "", text)[:200])

            code = AGENCY_CODE[first_agency(org)]
            seq_by_code[code] += 1
            doc_id = f"{code}-{seq_by_code[code]:03d}"
            pub, eff, dnote = dates_from_body(text)
            if dnote:
                note = (note + " " if note else "") + dnote
            # 施行日按法定默认回填（见 MANUAL_EFF 说明）：仅当解析器取不到、且发布日已有值时
            if eff is None and f in MANUAL_EFF and pub:
                eff = pub
                note = (note + " " if note else "") + MANUAL_EFF[f]
            # 采集时间以文件落盘时间近似 —— 本机下载后就地留存，没有别的记录
            fetched = int(datetime.date.fromtimestamp(os.path.getmtime(p)).strftime("%Y%m%d"))
            src = source_url_of(text)

            # 列序见 SPEC §2。expire_date / topic_tags 暂无可靠来源，留空不编造（见 SPEC §5.3）；
            # source_url 只填文件自身印着的（见 source_url_of）
            rows.append([doc_id, f, title, org, doc_no, dt, dom, status,
                         pub, eff, None, n, scheme,
                         src, fetched, None, os.path.relpath(dp, ROOT), note])

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "语料元数据"
# 第 6 列名用 doc_number（不是内部变量名 doc_no）——技术方案 §2.2 / 表8 / §8.1 Milvus
# Schema 三处都用 doc_number，字段名必须跟 P1 的 DDL 对齐。见 SPEC §7。
ws.append(["序号", "doc_id", "file_name", "doc_title", "org", "doc_number", "doc_type",
           "domain", "status", "publish_date", "effective_date", "expire_date",
           "clause_count", "clause_scheme", "source_url", "fetched_at", "topic_tags",
           "source_dir", "note"])
for i, r in enumerate(rows, 1):
    ws.append([i] + r)
for col, w in zip("ABCDEFGHIJKLMNOPQRS",
                  (5, 11, 46, 50, 30, 20, 11, 8, 13, 13, 13, 13, 12, 12, 40, 11, 20, 34, 40)):
    ws.column_dimensions[col].width = w
ws.freeze_panes = "A2"
os.makedirs(OUT_DIR, exist_ok=True)
# ★ 这一行必须挡在 `__main__` 后面。本脚本没有别的守卫，694 行全是模块级，
#   所以任何人写一句 `from _meta import AGENCY_CODE` 都会走到这里，把整张表
#   重新生成一遍 —— 而那会**抹掉 source_url（人工核验的 20 份）与 topic_tags
#   （第 6 步回标的 158 份）**，且一声不响。要复用这里的常量就复制，
#   别 import；真要 import 就把整个写入段都挪进 main()。
if __name__ == "__main__":
    wb.save(os.path.join(OUT_DIR, "语料元数据.xlsx"))

print("rows", len(rows))
print("clause_scheme", Counter(r[12] for r in rows).most_common())
print("domain", Counter(r[6] for r in rows).most_common())
print("doc_type", Counter(r[5] for r in rows).most_common())
print("status", Counter(r[7] for r in rows).most_common())
print("no_doc_number", sum(1 for r in rows if not r[4]))
print("doc_id 池", Counter(r[0].split("-")[0] for r in rows).most_common(),
      "共", len({r[0] for r in rows}))
print("有 publish_date", sum(1 for r in rows if r[8]))
print("有 effective_date", sum(1 for r in rows if r[9]))
print("有 fetched_at", sum(1 for r in rows if r[14]))
print("read_fail", sum(1 for r in rows if r[7] == -1))