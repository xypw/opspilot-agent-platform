"""PDF 解析与保守的页面质量标记；标记不能替代人工核对原件。"""

from pathlib import Path
from typing import BinaryIO
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PDFNeedsOCRError(ValueError):
    """PDF 没有可提取文本，需要 OCR 后才能进入知识库。"""


PARSER_VERSION = "pdf-pypdf-v2"


def _has_images(pdf_page: object) -> bool:
    """图片与文字混排需要复核；无法检查资源时不猜测页面结构。"""
    try:
        resources = pdf_page.get("/Resources")
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        for item in objects.values():
            item = item.get_object() if hasattr(item, "get_object") else item
            if item.get("/Subtype") == "/Image":
                return True
    except (AttributeError, TypeError, ValueError):
        pass
    return False


def _layout_reasons(pdf_page: object, text: str) -> list[str]:
    reasons = []
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return ["needs_ocr"]
    suspicious = sum(char == "\ufffd" or (ord(char) < 32 and char not in "\n\t\r")
                     for char in visible)
    if suspicious / len(visible) > 0.01:
        reasons.append("possible_garbled_text")
    if len(visible) < 12:
        reasons.append("unusually_short_text")
    if _has_images(pdf_page):
        reasons.append("mixed_image_text_layout")
    if len(re.findall(r"(?m)^\S.+\s{3,}\S", text)) >= 2:
        reasons.append("possible_table_or_columns")
    # 坐标只作风险提示。PDF 内容流顺序并不等于人眼阅读顺序。
    positions: list[tuple[float, float]] = []

    def record_position(fragment, _cm, tm, _font, _size):
        if fragment.strip() and len(tm) >= 6:
            positions.append((float(tm[4]), float(tm[5])))

    try:
        pdf_page.extract_text(visitor_text=record_position)
    except (TypeError, AttributeError, ValueError):
        positions = []
    if len(positions) >= 4:
        backward_jumps = sum(
            current[1] > previous[1] + 24
            for previous, current in zip(positions, positions[1:])
        )
        if backward_jumps >= 2:
            reasons.append("possible_reading_order_error")
        left = [y for x, y in positions if x < 250]
        right = [y for x, y in positions if x >= 250]
        if len(left) >= 2 and len(right) >= 2 and min(left) <= max(right) and min(right) <= max(left):
            reasons.append("possible_two_column_layout")
    return reasons


def parse_pdf(source: str | Path | BinaryIO) -> list[dict]:
    """读取 PDF 的每一页，返回页码和文本，不把不同页面提前合并。"""
    try:
        reader = PdfReader(source)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF 已加密，暂时无法解析")

        pages = []
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            text = (pdf_page.extract_text() or "").strip()
            reasons = _layout_reasons(pdf_page, text)
            pages.append({
                "page": page_number,
                "text": text,
                "needs_ocr": not bool(text),
                "validation_status": "needs_review" if reasons else "validated",
                "validation_reasons": reasons,
                "parser_version": PARSER_VERSION,
            })
        if not pages:
            raise ValueError("PDF 没有页面")
        return pages
    except ValueError:
        raise
    except (PdfReadError, OSError) as error:
        raise ValueError("PDF 文件无法读取或格式无效") from error
