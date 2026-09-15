"""离线核对程序生成的引用是否指向人工标注的标准来源。"""

from grounding_policy import append_verified_citations


def _source_ref(document: dict) -> str:
    title = document.get("title")
    page = document.get("page")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("评测文档缺少有效标题")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("评测文档缺少有效页码")
    return f"《{title}》第{page}页"


def evaluate_citation_sources(runs: list[dict], documents: list[dict], k: int) -> dict:
    """用真实引用格式化器计算严格标准来源精确率，而非模型回答正确率。"""
    if not isinstance(runs, list) or not runs:
        raise ValueError("runs 必须是非空列表")
    if not isinstance(documents, list) or not documents:
        raise ValueError("documents 必须是非空列表")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError("k 必须是正整数")

    by_id = {}
    for document in documents:
        chunk_id = document.get("chunk_id") if isinstance(document, dict) else None
        if not isinstance(chunk_id, str) or not chunk_id.strip() or chunk_id in by_id:
            raise ValueError("评测文档 chunk_id 必须唯一且非空")
        _source_ref(document)
        by_id[chunk_id] = document

    results = []
    case_ids = set()
    citation_count = 0
    gold_citation_count = 0
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("每条 run 都必须是字典")
        case_id = run.get("case_id")
        gold_id = run.get("expected_chunk_id")
        retrieved_ids = run.get("retrieved_chunk_ids")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids:
            raise ValueError("case_id 必须唯一且非空")
        if gold_id not in by_id:
            raise ValueError(f"{case_id}: 标准片段不在评测文档中")
        if not isinstance(retrieved_ids, list) or any(
            not isinstance(chunk_id, str) or chunk_id not in by_id
            for chunk_id in retrieved_ids
        ):
            raise ValueError(f"{case_id}: 检索片段编号无效")

        selected = [by_id[chunk_id] for chunk_id in retrieved_ids[:k]]
        rendered = append_verified_citations("离线引用检查", selected)
        citation_section = rendered.partition("\n\n来源：")[2]
        cited_refs = citation_section.split("；") if citation_section else []
        gold_ref = _source_ref(by_id[gold_id])
        gold_cited = gold_ref in cited_refs
        non_gold_refs = [ref for ref in cited_refs if ref != gold_ref]
        citation_count += len(cited_refs)
        gold_citation_count += int(gold_cited)
        case_ids.add(case_id)
        results.append({
            "case_id": case_id,
            "gold_chunk_id": gold_id,
            "gold_source": gold_ref,
            "cited_sources": cited_refs,
            "gold_source_cited": gold_cited,
            "non_gold_sources": non_gold_refs,
        })

    return {
        "case_count": len(runs),
        "citation_count": citation_count,
        "gold_citation_count": gold_citation_count,
        "gold_source_precision": (
            gold_citation_count / citation_count if citation_count else 0.0
        ),
        "gold_source_recall": gold_citation_count / len(runs),
        "results": results,
    }
