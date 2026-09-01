"""企业文档解析入口；当前先支持保留页码的 PDF 文本解析。"""

from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PDFNeedsOCRError(ValueError):
    """PDF 没有可提取文本，需要 OCR 后才能进入知识库。"""


def parse_pdf(source: str | Path | BinaryIO) -> list[dict]:
    """读取 PDF 的每一页，返回页码和文本，不把不同页面提前合并。"""
    try:
        reader = PdfReader(source)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF 已加密，暂时无法解析")

        pages = []
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            text = (pdf_page.extract_text() or "").strip()
            pages.append({
                "page": page_number,
                "text": text,
                "needs_ocr": not bool(text),
            })
        if pages and all(page["needs_ocr"] for page in pages):
            raise PDFNeedsOCRError("PDF 未提取到文字，可能是扫描件，需要先进行 OCR")
        return pages
    except ValueError:
        raise
    except (PdfReadError, OSError) as error:
        raise ValueError("PDF 文件无法读取或格式无效") from error
