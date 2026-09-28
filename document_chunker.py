"""优先沿 PDF 提取出的结构边界切块，并保留原文位置。"""

import re


HEADING = re.compile(r"^\s*(?:#{1,6}\s+|第[一二三四五六七八九十\d]+[章节条]|[一二三四五六七八九十]+、)")
LIST_ITEM = re.compile(r"^\s*(?:[-*•]\s+|\d+[.、]\s+)")
POLICY_RULE_LINE = re.compile(r"^\s*[^\n：:]{2,24}[：:]\S+")


def _heading_level(line: str) -> int:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return len(stripped) - len(stripped.lstrip("#"))
    if "章" in stripped[:12]:
        return 1
    if "节" in stripped[:12] or "、" in stripped[:6]:
        return 2
    return 3


def split_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """按字符切块；相邻片段保留 overlap 个字符的上下文。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")
    if not text:
        return []

    chunks = []
    step = chunk_size - overlap

    for start in range(0, len(text), step):
        chunk = text[start:start + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        # 已经包含文档结尾时停止，避免产生只有重叠尾部的冗余片段。
        if start + chunk_size >= len(text):
            break

    return chunks


def split_structured_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[dict]:
    """返回原文子串及偏移；过长单行才使用字符窗口。"""
    split_text("", chunk_size, overlap)  # 共用参数检查。
    if not text.strip():
        return []
    if "\n" not in text:
        step = chunk_size - overlap
        return [
            {"content": part, "source_start": start, "source_end": start + len(part),
             "heading_path": []}
            for start, part in ((i * step, chunk) for i, chunk in enumerate(split_text(text, chunk_size, overlap)))
        ]

    records: list[dict] = []
    start: int | None = None
    end = 0
    heading_path: list[str] = []
    active_heading: list[str] = []
    heading_stack: list[tuple[int, str]] = []

    def append_part(begin: int, finish: int, headings: list[str]) -> None:
        if text[begin:finish].strip():
            records.append({"content": text[begin:finish], "source_start": begin,
                            "source_end": finish, "heading_path": list(headings)})

    for match in re.finditer(r"[^\n]*(?:\n|$)", text):
        line = match.group()
        if not line:
            continue
        preceding = text[start:match.start()].rstrip() if start is not None else ""
        plain_heading = (bool(line.strip()) and len(line.strip()) <= 24
                         and not re.search(r"[。！？；：，,:|]", line)
                         and preceding.endswith(("。", "！", "？", "；")))
        is_heading = bool(HEADING.match(line)) or plain_heading
        is_boundary = (is_heading or bool(LIST_ITEM.match(line))
                       or bool(POLICY_RULE_LINE.match(line)) or "|" in line or not line.strip())
        if is_boundary and start is not None:
            append_part(start, end, heading_path)
            start = None
        if is_heading:
            level = _heading_level(line)
            heading_stack = [(depth, name) for depth, name in heading_stack if depth < level]
            heading_stack.append((level, line.strip()))
            active_heading = [name for _, name in heading_stack]
        if start is None:
            start, heading_path = match.start(), list(active_heading)
        if match.end() - start > chunk_size and start < match.start():
            append_part(start, match.start(), heading_path)
            start, heading_path = match.start(), list(active_heading)
        if match.end() - start > chunk_size:
            for offset in range(match.start(), match.end(), chunk_size - overlap):
                finish = min(offset + chunk_size, match.end())
                append_part(offset, finish, active_heading)
                if finish == match.end():
                    break
            start = None
        else:
            end = match.end()
        if not line.strip() and start is not None:
            append_part(start, end, heading_path)
            start = None
    if start is not None:
        append_part(start, end, heading_path)
    return records


def build_chunk_records(
    document_id: str,
    title: str,
    page: int,
    text: str,
    chunk_size: int = 500,
    overlap: int = 100,
) -> list[dict]:
    """为每个文本片段补充稳定编号和可引用的文档来源。"""
    if not document_id.strip() or not title.strip():
        raise ValueError("document_id 和 title 不能为空")
    if page < 1:
        raise ValueError("page 必须从 1 开始")

    records = []
    contents = split_structured_text(text, chunk_size=chunk_size, overlap=overlap)
    for index, part in enumerate(contents):
        records.append({
            "chunk_id": f"{document_id}-p{page}-c{index}",
            "title": title,
            "page": page,
            **part,
        })
    return records
