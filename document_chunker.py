"""把解析后的文档文本切成有重叠的片段，供后续 Embedding 和检索使用。"""


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
    contents = split_text(text, chunk_size=chunk_size, overlap=overlap)
    for index, content in enumerate(contents):
        records.append({
            "chunk_id": f"{document_id}-p{page}-c{index}",
            "title": title,
            "page": page,
            "content": content,
        })
    return records
