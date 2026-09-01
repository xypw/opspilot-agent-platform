"""文档入库流水线：把 PDF 页面转换成可检索、可引用的知识片段。"""

from pathlib import Path
from typing import BinaryIO

from document_chunker import build_chunk_records
from document_parser import parse_pdf


def ingest_pdf(
    source: str | Path | BinaryIO,
    document_id: str,
    title: str,
    chunk_size: int = 500,
    overlap: int = 100,
) -> dict:
    """解析并切分 PDF；需要 OCR 的页面单独报告，不静默丢失。"""
    pages = parse_pdf(source)
    records = []
    ocr_required_pages = []

    for page in pages:
        if page["needs_ocr"]:
            ocr_required_pages.append(page["page"])
            continue

        page_records = build_chunk_records(
            document_id=document_id,
            title=title,
            page=page["page"],
            text=page["text"],
            chunk_size=chunk_size,
            overlap=overlap,
        )
        for record in page_records:
            record["extraction_method"] = "text"
            record["ocr_confidence"] = None
        records.extend(page_records)

    return {
        "document_id": document_id,
        "title": title,
        "status": "partial" if ocr_required_pages else "ready",
        "page_count": len(pages),
        "chunk_count": len(records),
        "ocr_required_pages": ocr_required_pages,
        "chunks": records,
    }
