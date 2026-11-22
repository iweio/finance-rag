#!/usr/bin/env python3
"""finlaw_ocr.py — 法律/金融法规文档图片 OCR 转 Markdown

功能:
  - 自适应行聚类排序(按中位行高容差, 避免固定像素桶串行)
  - 编/章/节/条/项 层级识别, 段落与续行合并, 页码过滤
  - 条文渲染为"标题(仅第X条) + 正文段落", 正文不再被吞进标题
  - 印章掩膜(--remove-seal), 防止印章文字污染正文(面积阈值随分辨率自适应)
  - 表格识别(--tables, PP-StructureV3, 首次运行需联网下载模型; 支持合并单元格)
  - 多页跨段合并(--merge), 按自然页序排序

依赖:
    pip install "paddleocr>=3.0" paddlepaddle opencv-python numpy

用法:
    python finlaw_ocr.py page.png                              # 单页 -> page.md
    python finlaw_ocr.py p1.png p2.png p10.png                 # 批量, 每页一个 .md
    python finlaw_ocr.py p1.png p2.png --merge -o 民法典.md    # 多页合并为一份
    python finlaw_ocr.py scan.png --remove-seal                # 掩膜印章后识别
    python finlaw_ocr.py scan.png --remove-seal --blue-seal    # 含蓝色印章
    python finlaw_ocr.py report.png --tables                   # 识别表格

已知边界:
  - 不处理多栏排版; 无线表格识别率低, 输出后建议人工复核
  - 印章掩膜会一并抹掉被压住的文字, 请对照日志中的印章坐标人工复核
  - 默认关闭文档方向分类/矫正以提速; 倒置或严重倾斜的扫描件
    请把 get_ocr() 中对应开关改为 True
  - PP-StructureV3 的返回键名在不同小版本间可能不同, 表格输出
    为空时用 -v 打印 res.keys() 对照调整 run_ocr() 中取值三行
  - --tables 模式下 --lang 不生效(PP-StructureV3 使用默认识别语言)
"""
import argparse
import logging
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

import cv2
import numpy as np
from paddleocr import PaddleOCR, PPStructureV3

logger = logging.getLogger("finlaw_ocr")

# 表格框与正文行框的重叠面积占行框面积的比例, 超过即认为该行属于表格
TABLE_OVERLAP = 0.3

# ---------------------------------------------------------------- 数据模型

@dataclass
class OcrLine:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def height(self) -> float:
        return self.y1 - self.y0


# ---------------------------------------------------------------- 模型单例

@lru_cache(maxsize=4)
def get_ocr(lang: str = "ch") -> PaddleOCR:
    """模型单例: 避免每次调用重复加载检测/识别模型(每次数秒)"""
    logger.info("初始化 PaddleOCR (lang=%s) ...", lang)
    return PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        lang=lang,
        # Intel CPU 加速开关; 在 Intel CPU 上可改 True 提速,
        # 部分平台(旧 Mac / ARM)不支持, 需保持 False
        enable_mkldnn=False,
    )


@lru_cache(maxsize=1)
def get_structure() -> "PPStructureV3":
    """版面分析单例(含表格模型): 首次调用自动下载权重, 之后可离线使用"""
    logger.info("初始化 PPStructureV3(首次运行需联网下载模型) ...")
    return PPStructureV3()


# ---------------------------------------------------------------- 图像预处理: 印章掩膜

def remove_stamps(bgr: np.ndarray, blue: bool = False) -> tuple:
    """检测并抹除印章, 返回 (抹除后的图, 印章框列表[x1,y1,x2,y2])。

    原理: 印章为红色(或蓝色)且连通域近似方形/圆形、带镂空;
    红头文件的红色标题是细长条, 用形态过滤与印章区分开。
    面积阈值随图像分辨率自适应, 避免小图漏检。"""
    h_img, w_img = bgr.shape[:2]
    min_area = max(400.0, h_img * w_img * 2e-4)  # ~200dpi A4 扫描件约等于旧的固定 800px
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # 红色在 HSV 中跨 0 轴, 需两段区间
    mask = (cv2.inRange(hsv, (0, 70, 70), (12, 255, 255))
            | cv2.inRange(hsv, (168, 70, 70), (180, 255, 255)))
    if blue:
        mask |= cv2.inRange(hsv, (100, 70, 70), (130, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    out = bgr.copy()
    stamps = []
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        ratio = w / max(h, 1)
        # 方形/圆形 + 有镂空 + 面积足够 => 印章; 细长条(红头标题/红笔迹)排除
        if 0.6 <= ratio <= 1.8 and area > min_area and 0.2 < area / (w * h) < 0.95:
            comp = labels[y:y + h, x:x + w] == i
            region = out[y:y + h, x:x + w]
            region[comp] = (255, 255, 255)
            stamps.append((x, y, x + w, y + h))
    return out, stamps


def load_image(image_path: Path) -> np.ndarray:
    """读取图片; 用 imdecode 兼容含中文/非 ASCII 字符的路径"""
    data = np.fromfile(str(image_path), dtype=np.uint8)
    if data.size == 0:
        raise ValueError(f"图片为空或读取失败: {image_path}")
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法解码图片(格式不支持或已损坏): {image_path}")
    return img


# ---------------------------------------------------------------- OCR 层

def _overlap_ratio(line: OcrLine, region: list) -> float:
    """行框与区域(表格)的重叠面积占行框面积的比例。

    比"中心点是否落入"更稳健: 跨出表格边界的长行只要大部分在表内
    仍会交给表格路, 不会正文、表格各出现一份。"""
    ix0, iy0 = max(line.x0, region[0]), max(line.y0, region[1])
    ix1, iy1 = min(line.x1, region[2]), min(line.y1, region[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = max((line.x1 - line.x0) * (line.y1 - line.y0), 1e-6)
    return inter / area


def _collect_lines(result, min_score: float, exclude_boxes=None) -> list:
    """从 predict 结果提取 OcrLine(未排序), 支持按排除区域(表格)过滤"""
    raw = []
    for res in result:
        texts = res.get("rec_texts") or []
        scores = res.get("rec_scores") or []
        boxes = res.get("rec_boxes")
        if boxes is None:
            boxes = res.get("rec_polys")
        if boxes is None or len(boxes) == 0:
            continue
        n = min(len(texts), len(scores), len(boxes))
        if n < len(texts):
            logger.warning("rec_texts/rec_scores/rec_boxes 长度不一致, "
                           "按最短长度 %d 截断", n)
        for i in range(n):
            text, score, box = texts[i], float(scores[i]), boxes[i]
            if score < min_score:           # 低置信度噪声(印章残字/水印)丢弃
                continue
            pts = np.asarray(box, dtype=float).reshape(-1, 2)
            line = OcrLine(
                text=text, score=score,
                x0=float(pts[:, 0].min()), y0=float(pts[:, 1].min()),
                x1=float(pts[:, 0].max()), y1=float(pts[:, 1].max()),
            )
            if exclude_boxes and any(_overlap_ratio(line, r) > TABLE_OVERLAP
                                     for r in exclude_boxes):
                continue                    # 表格内的文字交给表格路处理
            raw.append(line)
    return raw


def run_ocr(image_path, min_score: float = 0.5, lang: str = "ch",
            remove_seal: bool = False, blue_seal: bool = False,
            with_tables: bool = False) -> tuple:
    """OCR 并排序, 返回 (阅读顺序的 OcrLine 列表, [(表格bbox, md表格), ...])"""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")

    img = load_image(image_path)
    if remove_seal:
        img, stamps = remove_stamps(img, blue=blue_seal)
        for s in stamps:
            logger.info("%s: 检测到印章区域 %s, 已掩膜(注意: 被压住的文字一并抹除)",
                        image_path.name, s)

    if with_tables:
        if lang != "ch":
            logger.warning("--tables 模式使用 PPStructureV3 默认识别语言, "
                           "忽略 --lang=%s", lang)
        # PP-StructureV3 内含整页 OCR, 一次预测同时拿到正文行和表格,
        # 不重复调用 PaddleOCR
        try:
            res = get_structure().predict(img)[0]
        except Exception as exc:
            raise RuntimeError(f"版面分析失败 ({image_path.name}): {exc}") from exc

        tables = []
        for t in res.get("table_res_list", []) or []:
            md = html_table_to_md(t.get("pred_html") or "")
            if md:
                tables.append((list(map(float, t["bbox"])), md))

        ocr_res = res.get("overall_ocr_res") or {}
        pseudo = [{
            "rec_texts": ocr_res.get("rec_texts") or [],
            "rec_scores": ocr_res.get("rec_scores") or [],
            "rec_polys": ocr_res.get("rec_polys") or ocr_res.get("rec_boxes"),
        }]
        lines = _collect_lines(pseudo, min_score,
                               exclude_boxes=[b for b, _ in tables] or None)
        return group_lines(lines), tables

    ocr = get_ocr(lang)
    try:
        result = ocr.predict(img)           # PaddleOCR 支持 ndarray 输入
    except Exception as exc:
        raise RuntimeError(f"OCR 推理失败 ({image_path.name}): {exc}") from exc
    if not result:
        logger.warning("%s: OCR 未返回任何结果", image_path.name)
    return group_lines(_collect_lines(result or [], min_score)), []


def group_lines(lines: list) -> list:
    """自适应行聚类: 容差取中位行高的 0.6 倍。
    先按 y 排序聚类成行, 行内按 x 排序后拼接, 避免相邻行交叉混排。"""
    if not lines:
        return []
    lines = sorted(lines, key=lambda l: (l.y0, l.x0))
    median_h = sorted(l.height for l in lines)[len(lines) // 2]
    tol = max(4.0, median_h * 0.6)

    rows, cur, ref_y = [], [], None
    for ln in lines:
        if ref_y is None or abs(ln.y0 - ref_y) <= tol:
            cur.append(ln)
            ref_y = ln.y0 if ref_y is None else (ref_y + ln.y0) / 2
        else:
            rows.append(merge_row(sorted(cur, key=lambda l: l.x0)))
            cur, ref_y = [ln], ln.y0
    if cur:
        rows.append(merge_row(sorted(cur, key=lambda l: l.x0)))
    return rows


def merge_row(row: list) -> OcrLine:
    """同一视觉行内被 OCR 拆开的多个框拼接为一个逻辑行; 大间距处补空格"""
    first = row[0]
    parts, x1, y1, score = [first.text], first.x1, first.y1, first.score
    for ln in row[1:]:
        if ln.x0 - x1 > max(first.height, ln.height) * 0.8:
            parts.append(" ")
        parts.append(ln.text)
        x1, y1 = max(x1, ln.x1), max(y1, ln.y1)
        score = min(score, ln.score)
    return OcrLine("".join(parts), score, first.x0, first.y0, x1, y1)


# ---------------------------------------------------------------- 文本清洗

CJK = r"\u4e00-\u9fff"
NUM_ZH = "一二三四五六七八九十百千零〇两"

PAGE_NUM_RE = re.compile(r"^[-—–=·\s]*\d{1,4}\s*[-—–=·\s]*$")
PAGE_WORD_RE = re.compile(r"^第\s*\d+\s*页(\s*共\s*\d+\s*页)?$")

DIV_RE = re.compile(rf"^第\s*[{NUM_ZH}\d]+\s*编")
CHAPTER_RE = re.compile(rf"^第\s*[{NUM_ZH}\d]+\s*章")
SECTION_RE = re.compile(rf"^第\s*[{NUM_ZH}\d]+\s*节")
ARTICLE_RE = re.compile(rf"^第\s*[{NUM_ZH}\d]+\s*条")
# 条文标记可能带"之一/之二"后缀(如"第一百五十八条之一")
ARTICLE_MARKER_RE = re.compile(rf"^第\s*[{NUM_ZH}\d]+\s*条(?:\s*之\s*[{NUM_ZH}]+)?")
ITEM_RE = re.compile(rf"^[（(]\s*[{NUM_ZH}]+\s*[）)]")
PAREN_NUM_RE = re.compile(r"^[（(]\s*\d{1,2}\s*[）)]")
# 编号限 1~2 位且小数点后不跟数字, 避免 "3.15" / "2021.01" / "3．5%" 误判
NUM_ITEM_RE = re.compile(r"^\d{1,2}\s*(?:[、)]|[.．](?!\d))\s*")

BASE_TERMINAL = tuple("。；？！…．")          # 真正的句末标点
_CLOSERS = "”’）》】』」"                     # 跟在句末标点后的收尾符

HEAD_MARKER_RE = re.compile(rf"^(第[\s\d{NUM_ZH}]+[编章节条])(.*)$")
ITEM_MARKER_RE = re.compile(rf"^([（(][\s\d{NUM_ZH}]+[）)])(.*)$")


def clean_text(text: str) -> str:
    """去首尾空白、合并 CJK 之间的 OCR 空格、归一化编号内部空格与全半角括号"""
    text = text.strip()
    text = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", text)
    m = HEAD_MARKER_RE.match(text)
    if m:
        return re.sub(r"\s+", "", m.group(1)) + m.group(2)
    m = ITEM_MARKER_RE.match(text)
    if m:
        head = (re.sub(r"\s+", "", m.group(1))
                .replace("(", "（").replace(")", "）"))
        return head + m.group(2)
    return text


def is_page_number(text: str) -> bool:
    return bool(PAGE_NUM_RE.match(text) or PAGE_WORD_RE.match(text))


def ends_complete(text: str) -> bool:
    """行是否以终止标点收尾。右引号/右括号不算句末,
    避免 "他说"你好"" 这类行被误判为段落结束而切断续行合并。"""
    return text.rstrip(_CLOSERS).endswith(BASE_TERMINAL)


# ---------------------------------------------------------------- 结构层

# 页内/跨页可拼接续行的类型(不含 title/div/chapter/section/article,
# 防止正文续到标题上, 或把整条正文吞进"第X条"标题)
MERGEABLE = ("text", "item", "num_item")


def classify(text: str) -> str:
    if DIV_RE.match(text):
        return "div"
    if CHAPTER_RE.match(text):
        return "chapter"
    if SECTION_RE.match(text):
        return "section"
    if ARTICLE_RE.match(text):
        return "article"
    if ITEM_RE.match(text):
        return "item"
    if PAREN_NUM_RE.match(text) or NUM_ITEM_RE.match(text):
        return "num_item"
    return "text"


def restore_structure(lines: list) -> list:
    """单页内结构还原: 分类 + 页内续行合并(标题识别在跨页合并后统一做)。"""
    blocks = []
    for ln in lines:
        text = clean_text(ln.text)
        if not text or is_page_number(text):
            continue
        btype = classify(text)
        if btype == "article":
            m = ARTICLE_MARKER_RE.match(text)
            if m and m.end() < len(text):
                # "第X条 正文同层" -> 拆成 标题块 + 正文块,
                # 否则整段正文会被渲染成一个五级标题
                blocks.append({"type": "article", "content": m.group(0),
                               "score": ln.score, "y": ln.y0})
                text = text[m.end():].strip()
                if not text:
                    continue
                btype = "text"
            else:
                # 条标题独占一行(可能在页尾), 正文从后续行/下一页开始
                blocks.append({"type": "article", "content": text,
                               "score": ln.score, "y": ln.y0})
                continue
        if (btype == "text" and blocks
                and blocks[-1]["type"] in MERGEABLE
                and not ends_complete(blocks[-1]["content"])):
            blocks[-1]["content"] += text
            blocks[-1]["score"] = min(blocks[-1]["score"], ln.score)
            continue
        blocks.append({"type": btype, "content": text,
                       "score": ln.score, "y": ln.y0})
    return blocks


def merge_blocks_across_pages(blocks_pages: list) -> list:
    """跨页归并: 仅当上一页末块未以终止标点结尾时, 与下一页首个 text 块拼接。

    页码已在单页层过滤, 不会打断判断;
    条标题独占页尾时, 下页正文会紧跟在"第X条"标题块之后成为独立段落。"""
    merged: list = []
    for blocks in blocks_pages:
        for b in blocks:
            if (merged
                    and b["type"] == "text"
                    and merged[-1]["type"] in MERGEABLE
                    and not ends_complete(merged[-1]["content"])):
                merged[-1]["content"] += b["content"]
                merged[-1]["score"] = min(merged[-1]["score"], b["score"])
                continue
            merged.append(dict(b))
    return merged


def detect_title(blocks: list) -> list:
    """文档标题: 第一个编/章/节/条标题出现之前的第一个 text 块。
    在跨页合并之后统一执行, 避免多页模式下每页首行都被当成标题。"""
    for b in blocks:
        if b["type"] in ("div", "chapter", "section", "article"):
            break
        if b["type"] == "text":
            b["type"] = "title"
            break
    return blocks


# ---------------------------------------------------------------- 表格

class _TableHTMLParser(HTMLParser):
    """极简表格解析: 抽取 tr/td/th 及其 colspan/rowspan(标准库, 比正则稳健)"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list = []
        self._row = None
        self._cell = None
        self._cs = 1
        self._rs = 1

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "tr":
            self._row = []
        elif t in ("td", "th") and self._row is not None:
            self._cell = []
            self._cs = self._rs = 1
            for k, v in attrs:
                if k == "colspan" and v and v.isdigit():
                    self._cs = max(1, int(v))
                elif k == "rowspan" and v and v.isdigit():
                    self._rs = max(1, int(v))

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("td", "th") and self._row is not None and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self._row.append((text, self._cs, self._rs))
            self._cell = None
        elif t == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _expand_grid(parsed_rows: list) -> list:
    """把 [(文本, colspan, rowspan), ...] 的二维列表展开为规则网格"""
    grid, pending = [], []  # pending: [起始列, 剩余行数, 文本, colspan]
    for cells in parsed_rows:
        row, col, occupied = [], 0, set()
        for p in pending:                       # 先落上一行 rowspan 留下的单元格
            c, _, text, cs = p
            row.extend([""] * (c + cs - len(row)))
            for k in range(cs):
                row[c + k] = text
                occupied.add(c + k)
            p[1] -= 1
        pending = [p for p in pending if p[1] > 0]
        for text, cs, rs in cells:
            while col in occupied:              # 让开被 rowspan 占据的列
                col += 1
            row.extend([""] * (col + cs - len(row)))
            for k in range(cs):
                row[col + k] = text
                occupied.add(col + k)
            if rs > 1:
                pending.append([col, rs - 1, text, cs])
            col += cs
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    for r in grid:
        r.extend([""] * (width - len(r)))
    return grid


def html_table_to_md(html: str) -> str:
    """PP-Structure 的 pred_html 转 Markdown 表格;
    展开 colspan/rowspan, 转义单元格内的管道符。"""
    parser = _TableHTMLParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:                    # 畸形 HTML 视为无表格
        logger.warning("表格 HTML 解析失败, 跳过该表格: %s", exc)
        return ""
    grid = _expand_grid(parser.rows)
    if not grid:
        return ""
    lines = ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"
             for row in grid]
    lines.insert(1, "|" + " --- |" * len(grid[0]))
    return "\n".join(lines)


def insert_tables(blocks: list, tables: list) -> list:
    """按表格顶部 y 坐标把表格块插回正文对应位置"""
    out = list(blocks)
    for bbox, md in sorted(tables, key=lambda t: t[0][1]):
        idx = next((i for i, b in enumerate(out) if b.get("y", 0) > bbox[1]),
                   len(out))
        out.insert(idx, {"type": "table", "content": md, "y": bbox[1]})
    return out


# ---------------------------------------------------------------- Markdown 渲染

HEADING_LEVEL = {"title": 1, "div": 2, "chapter": 3, "section": 4, "article": 5}
_MD_STRUCT_RE = re.compile(r"^(\s*)(\*{1,3}|[-+]|>+|#{1,6}|\d{1,3}[.)])(\s|$)")


def escape_md(text: str) -> str:
    """转义反引号、星号及行首会被误认为 Markdown 结构的字符。
    数字编号(1. / 2))只转义标点本身, 否则反斜杠会出现在数字前而显示出来。"""
    text = text.replace("`", "\\`").replace("*", "\\*")

    def _repl(m):
        head = m.group(2)
        if head[0].isdigit() and head[-1] in ".)":
            return m.group(1) + head[:-1] + "\\" + head[-1] + m.group(3)
        return m.group(1) + "\\" + head + m.group(3)

    return _MD_STRUCT_RE.sub(_repl, text, count=1)


def blocks_to_markdown(blocks: list) -> str:
    md, in_list = [], False
    for b in blocks:
        btype, content = b["type"], b["content"]
        if btype in HEADING_LEVEL:
            in_list = False
            md += ["", f"{'#' * HEADING_LEVEL[btype]} {escape_md(content)}", ""]
        elif btype in ("item", "num_item"):
            content = escape_md(content)
            if btype == "num_item":
                # "- 1. x" 会被解析成嵌套有序列表, 转义编号
                # (escape_md 已处理行首数字的情况, 这里兜底半角编号)
                content = re.sub(r"^(\d+)([.)])", r"\1\\\2", content, count=1)
            indent = "  " if (btype == "num_item" and in_list) else ""
            in_list = True
            md.append(f"{indent}- {content}")
        elif btype == "table":
            in_list = False
            md += ["", content, ""]
        else:
            in_list = False
            md.append(escape_md(content))
    return "\n".join(md).strip() + "\n"


# ---------------------------------------------------------------- 流水线

def _page_blocks(image_path, min_score: float, lang: str,
                 remove_seal: bool, blue_seal: bool,
                 with_tables: bool) -> list:
    """单页处理公共部分: OCR -> 结构还原 -> 表格插回"""
    lines, tables = run_ocr(image_path, min_score=min_score, lang=lang,
                            remove_seal=remove_seal, blue_seal=blue_seal,
                            with_tables=with_tables)
    blocks = restore_structure(lines)
    if tables:
        blocks = insert_tables(blocks, tables)
    return blocks


def image_to_markdown(image_path, min_score: float = 0.5, lang: str = "ch",
                      remove_seal: bool = False, blue_seal: bool = False,
                      with_tables: bool = False) -> str:
    """单页转换; 出错抛异常(由调用方决定如何处理)"""
    blocks = _page_blocks(image_path, min_score, lang,
                          remove_seal, blue_seal, with_tables)
    if not blocks:
        logger.warning("%s: 无有效内容, 输出为空", image_path)
        return ""
    return blocks_to_markdown(detect_title(blocks))


def images_to_markdown(image_paths, min_score: float = 0.5, lang: str = "ch",
                       remove_seal: bool = False, blue_seal: bool = False,
                       with_tables: bool = False) -> tuple:
    """多页合并转换; 单页失败跳过并记日志, 不中断整个文档。

    返回 (markdown, 成功页数, 总页数), 供调用方决定退出码。"""
    blocks_pages, ok = [], 0
    for p in image_paths:
        try:
            blocks = _page_blocks(p, min_score, lang,
                                  remove_seal, blue_seal, with_tables)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error("跳过 %s: %s", p, exc)
            continue
        ok += 1
        logger.info("已处理 %s (%d 块)", p, len(blocks))
        if blocks:
            blocks_pages.append(blocks)
    if not blocks_pages:
        logger.warning("所有页面均无有效文本")
        return "", ok, len(image_paths)
    merged = merge_blocks_across_pages(blocks_pages)
    return blocks_to_markdown(detect_title(merged)), ok, len(image_paths)


# ---------------------------------------------------------------- CLI

def natural_key(p: Path) -> list:
    """自然排序键: page2.png 排在 page10.png 之前, 保证合并页序正确"""
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", p.stem)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="法律文档图片 OCR 转 Markdown")
    parser.add_argument("images", nargs="+", type=Path, help="输入图片路径")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="输出路径: --merge 时为合并文件; 单图时为该图的输出")
    parser.add_argument("--merge", action="store_true",
                        help="多张图片按页序跨页合并为一份 Markdown")
    parser.add_argument("--tables", action="store_true",
                        help="启用 PP-StructureV3 识别表格(首次需联网下载模型)")
    parser.add_argument("--remove-seal", action="store_true",
                        help="识别前掩膜红色印章, 防止印章文字污染正文")
    parser.add_argument("--blue-seal", action="store_true",
                        help="同时掩膜蓝色印章(配合 --remove-seal 使用)")
    parser.add_argument("--min-score", type=float, default=0.5,
                        help="置信度过滤阈值, 默认 0.5")
    parser.add_argument("--lang", default="ch", help="OCR 语言, 默认 ch")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")

    images = sorted(args.images, key=natural_key)

    if args.merge:
        out = args.output or images[0].with_name(images[0].stem + "_merged.md")
        md, ok, total = images_to_markdown(images, min_score=args.min_score,
                                           lang=args.lang,
                                           remove_seal=args.remove_seal,
                                           blue_seal=args.blue_seal,
                                           with_tables=args.tables)
        if not md.strip():
            logger.error("无有效内容, 未生成 %s", out)
            return 1
        try:
            out.write_text(md, encoding="utf-8")
        except OSError as exc:
            logger.error("写入 %s 失败: %s", out, exc)
            return 1
        if ok < total:
            logger.warning("合并完成, 但 %d/%d 页处理失败, 结果可能不完整",
                           total - ok, total)
        logger.info("已合并 %d 页 -> %s (%d 字符)", ok, out, len(md))
        return 1 if ok < total else 0

    if args.output and len(images) > 1:
        logger.warning("多图且未加 --merge: -o/--output 被忽略, 按每图 .md 输出")
    failed = 0
    for img in images:
        out = args.output if (args.output and len(images) == 1) \
            else img.with_suffix(".md")
        try:
            md = image_to_markdown(img, min_score=args.min_score, lang=args.lang,
                                   remove_seal=args.remove_seal,
                                   blue_seal=args.blue_seal,
                                   with_tables=args.tables)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error("%s", exc)
            failed += 1
            continue
        try:
            out.write_text(md, encoding="utf-8")
        except OSError as exc:
            logger.error("写入 %s 失败: %s", out, exc)
            failed += 1
            continue
        logger.info("%s -> %s (%d 字符)", img.name, out, len(md))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())