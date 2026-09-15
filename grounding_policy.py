"""RAG 可靠性规则：没有知识库证据时不让模型猜测。"""

NO_EVIDENCE_ANSWER = (
    "知识库中没有找到足够证据，暂时无法可靠回答。"
    "你可以联系人工客服，或留言补充问题。"
)


def answer_when_evidence_is_missing(tool_name: str, result: object) -> str | None:
    """知识检索返回空列表时给出确定性拒答，其他工具不受影响。"""
    if tool_name == "search_knowledge_base" and result == []:
        return NO_EVIDENCE_ANSWER
    return None


def append_verified_citations(answer: str, evidence: list[dict]) -> str:
    """只使用检索结果的元数据生成去重后的引用，不让模型编造来源。"""
    citations = []
    for item in evidence:
        title = item.get("title")
        page = item.get("page")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("知识库证据缺少有效标题。")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValueError("知识库证据缺少有效页码。")
        citation = f"《{title}》第{page}页"
        if citation not in citations:
            citations.append(citation)

    if not citations:
        return answer
    return f"{answer.rstrip()}\n\n来源：{'；'.join(citations)}"
