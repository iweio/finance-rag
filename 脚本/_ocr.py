"""OCR 适配层：扫描件 PDF → 与文本层 PDF 完全相同的「页文本」格式。

`finlaw_ocr.py` 面向图片、输出 Markdown；本模块负责三件事：

1. `is_scanned_pages`：判定一页组是不是扫描件（无文本层 / 平均每页 <100 字符）。
2. `pdf_pages_via_ocr`：fitz 把 PDF 逐页栅格成 PNG，逐页调 `finlaw_ocr.image_to_markdown`
   （印章掩膜 + PP-StructureV3 表格），返回 **list[页文本]**，与 `_kb.read_pages`
   对文本层 PDF 的返回结构一致，下游清洗 / 锚点 / 切分零改动。
3. `markdown_to_page_text`：把 finlaw_ocr 的 Markdown 还原成纯文本行 ——
   标题去 `#`、列表去 `- `、表格分隔行（`| --- |`）删除、Markdown 表行原样保留
   （`is_table_line` 认 `| ... |`，锚点正则由 `mask_table_lines` 屏蔽）。

finlaw_ocr 在函数内惰性导入：它顶层 import paddleocr（重），普通文本层 PDF
不走到 OCR 路径时不应付出 import 开销；paddleocr 未安装时报错并提示安装。

对应契约：SPEC.md §3.5（取文本规则）。
"""
import logging
import os
import re
import tempfile

import fitz

logger = logging.getLogger("_ocr")

# 判定阈值：文本层 PDF 即使表格多，平均每页也远超 100 字符；
# 扫描件 get_text() 通常只有页眉页脚几个字符
SCAN_AVG_CHARS = 100

# 栅格化 DPI：200 是 OCR 速度与字号的平衡点，再低小字号识别率明显掉
OCR_DPI = 200

_RE_HEADING = re.compile(r"^#{1,6}\s+")
_RE_TABLE_SEP = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_RE_LIST_ITEM = re.compile(r"^\s*-\s+")


def is_scanned_pages(pages: list[str]) -> bool:
    """判扫描件：页数 >0 且平均每页非空字符 < SCAN_AVG_CHARS。"""
    if not pages:
        return True
    avg = sum(len(pg.strip()) for pg in pages) / len(pages)
    return avg < SCAN_AVG_CHARS


def markdown_to_page_text(md: str) -> str:
    """finlaw_ocr 的单页 Markdown → 纯文本行（保表行、去结构标记）。"""
    lines: list[str] = []
    for ln in md.split("\n"):
        ln = ln.rstrip()
        if not ln.strip():
            continue
        if _RE_TABLE_SEP.match(ln):          # Markdown 表格分隔行不是正文
            continue
        if ln.lstrip().startswith("|"):      # 表行原样保留（含单元格内 \| 转义）
            lines.append(ln.strip())
            continue
        ln = _RE_HEADING.sub("", ln)         # 第X条 标题去 # 号 → 行首锚点可命中
        ln = _RE_LIST_ITEM.sub("", ln)       # - （一）… → （一）…，项号锚点可命中
        ln = ln.replace("\\*", "*").replace("\\`", "`")
        ln = re.sub(r"(\d)\\([.)])", r"\1\2", ln)   # 1\. → 1.（escape_md 的编号转义）
        ln = ln.replace("\\|", "|")
        if ln.strip():
            lines.append(ln)
    return "\n".join(lines)


def pdf_pages_via_ocr(p: str, dpi: int = OCR_DPI, with_tables: bool = True,
                      remove_seal: bool = True, blue_seal: bool = False,
                      min_score: float = 0.5) -> list[str]:
    """扫描件 PDF → OCR → list[页文本]。结构同 `_kb.read_pages` 的 PDF 分支。"""
    try:
        import finlaw_ocr
    except ImportError as exc:
        raise RuntimeError(
            "扫描件需要 OCR，但 finlaw_ocr / paddleocr 不可用："
            "pip install \"paddleocr>=3.0\" paddlepaddle opencv-python"
        ) from exc

    pages: list[str] = []
    with fitz.open(p) as d, tempfile.TemporaryDirectory(prefix="finlaw_") as td:
        for i, pg in enumerate(d):
            png = os.path.join(td, f"page-{i + 1}.png")
            pix = pg.get_pixmap(dpi=dpi)
            pix.save(png)
            try:
                md = finlaw_ocr.image_to_markdown(
                    png, min_score=min_score, remove_seal=remove_seal,
                    blue_seal=blue_seal, with_tables=with_tables)
            except RuntimeError:
                if not with_tables:
                    raise
                # 版面分析（含表格模型）失败时降级为纯文本 OCR，保住正文
                logger.exception("%s 第 %d 页版面分析失败，降级为纯文本 OCR",
                                 os.path.basename(p), i + 1)
                md = finlaw_ocr.image_to_markdown(
                    png, min_score=min_score, remove_seal=remove_seal,
                    blue_seal=blue_seal, with_tables=False)
            pages.append(markdown_to_page_text(md))
            logger.info("%s 第 %d/%d 页 OCR 完成 (%d 字符)",
                        os.path.basename(p), i + 1, len(d), len(pages[-1]))
    return pages
