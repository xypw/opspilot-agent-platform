"""离线评测单个候选片段能否通过指定的证据过滤规则。"""

from collections.abc import Callable

from evidence_support import filter_evidence_for_question


EvidenceFilter = Callable[[str, list[dict]], list[dict]]


def evaluate_evidence_sufficiency(
    cases: list[dict],
    evidence_filter: EvidenceFilter = filter_evidence_for_question,
) -> dict:
    """返回人工标签与规则判断的逐例结果和混淆矩阵。"""
    if not isinstance(cases, list) or not cases:
        raise ValueError("证据充分性评测集必须是非空列表")

    seen_ids = set()
    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    results = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("每条评测用例必须是字典")
        case_id = case.get("case_id")
        question = case.get("question")
        content = case.get("candidate_content")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError("case_id 必须是唯一的非空字符串")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{case_id}: question 必须是非空字符串")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{case_id}: candidate_content 必须是非空字符串")
        if type(case.get("sufficient")) is not bool:
            raise ValueError(f"{case_id}: sufficient 必须是布尔值")

        admitted = bool(evidence_filter(question, [{"content": content}]))
        outcome = ("tp" if admitted else "fn") if case["sufficient"] else (
            "fp" if admitted else "tn"
        )
        counts[outcome] += 1
        results.append({
            "case_id": case_id,
            "sufficient": case["sufficient"],
            "admitted": admitted,
            "outcome": outcome,
        })
        seen_ids.add(case_id)

    return {"case_count": len(cases), "counts": counts, "results": results}
