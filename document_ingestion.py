"""文档入库流水线：把 PDF 页面转换成可检索、可引用的知识片段。"""

from pathlib import Path
from typing import BinaryIO
import re

from document_chunker import build_chunk_records
from document_parser import parse_pdf


CRITICAL = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|天|日|小时|%)|第[一二三四五六七八九十\d]+条|"
    r"不得|不能|不可|未|不予|除非|如果|若"
)


def _chunk_reasons(text: str, record: dict, critical_spans: list[tuple[int, int]]) -> list[str]:
    start, end = record["source_start"], record["source_end"]
    if text[start:end] != record["content"]:
        return ["source_span_mismatch"]
    reasons = []
    if start and re.search(r"(?:不|未|若|如果|除非)$", text[max(0, start - 12):start]):
        reasons.append("possible_condition_boundary_split")
    if end < len(text) and re.search(r"(?:不|未|若|如果|除非|\d)$", text[max(0, end - 12):end]):
        reasons.append("possible_condition_boundary_split")
    # 检查同一条关键字段是否跨越切块边界；不能据此证明 PDF 解析正确。
    for critical_start, critical_end in critical_spans:
        if critical_start < end < critical_end or critical_start < start < critical_end:
            reasons.append("critical_field_split")
            break
    return sorted(set(reasons))


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
    page_audits = []
    cross_page_risks: set[int] = set()
    for previous, current in zip(pages, pages[1:]):
        previous_lines = previous["text"].splitlines()
        current_lines = current["text"].splitlines()
        previous_tail = previous_lines[-1].strip() if previous_lines else ""
        current_head = current_lines[0].strip() if current_lines else ""
        if previous_tail and current_head and (
            ("|" in previous_tail and "|" in current_head)
            or previous_tail.endswith(("：", ":", "，", ","))
        ):
            cross_page_risks.update((previous["page"], current["page"]))

    for page in pages:
        page_reasons = list(page.get("validation_reasons", []))
        if page["page"] in cross_page_risks:
            page_reasons.append("possible_cross_page_structure")
        if page["needs_ocr"]:
            ocr_required_pages.append(page["page"])
            if "needs_ocr" not in page_reasons:
                page_reasons.append("needs_ocr")
            page_audits.append({"page": page["page"], "text": page["text"],
                                "validation_status": "needs_review",
                                "validation_reasons": page_reasons,
                                "parser_version": page.get("parser_version", "pdf-pypdf-v2")})
            continue

        page_records = build_chunk_records(
            document_id=document_id,
            title=title,
            page=page["page"],
            text=page["text"],
            chunk_size=chunk_size,
            overlap=overlap,
        )
        critical_matches = list(CRITICAL.finditer(page["text"]))
        critical_spans = [(match.start(), match.end()) for match in critical_matches]
        for index, record in enumerate(page_records):
            record["chunk_index"] = len(records) + index
            record["extraction_method"] = "text"
            record["ocr_confidence"] = None
            record["parser_version"] = page.get("parser_version", "pdf-pypdf-v2")
            reasons = _chunk_reasons(page["text"], record, critical_spans)
            record["validation_reasons"] = reasons
            record["validation_status"] = "needs_review" if reasons else "validated"
        critical_terms = [
            {"text": match.group(), "source_start": match.start(), "source_end": match.end()}
            for match in critical_matches
        ]
        if any(not any(chunk["source_start"] <= term["source_start"]
                           and chunk["source_end"] >= term["source_end"]
                           for chunk in page_records) for term in critical_terms):
            page_reasons.append("critical_field_omitted_or_split")
        if any(record["validation_status"] == "needs_review" for record in page_records):
            page_reasons.append("chunk_boundary_review")
        page_audits.append({"page": page["page"], "text": page["text"],
                            "validation_status": "needs_review" if page_reasons else "validated",
                            "validation_reasons": page_reasons,
                            "parser_version": page.get("parser_version", "pdf-pypdf-v2"),
                            "critical_terms": critical_terms})
        records.extend(page_records)

    ready = bool(records) and all(page["validation_status"] == "validated" for page in page_audits)
    return {
        "document_id": document_id,
        "title": title,
        "status": "ready" if ready else "partial",
        "publication_status": "published" if ready else "pending_review",
        "page_count": len(pages),
        "chunk_count": len(records),
        "ocr_required_pages": ocr_required_pages,
        "chunks": records,
        "pages": page_audits,
    }
