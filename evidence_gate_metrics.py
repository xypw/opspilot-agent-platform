"""用人工标注和历史检索分数，离线比较单一相似度门槛。"""

import math


def evaluate_thresholds(cases: list[dict], thresholds: list[float]) -> list[dict]:
    """返回各门槛的 TP/FN/FP/TN；不运行检索，也不评价最终答案。"""
    if not cases or not thresholds:
        raise ValueError("评测用例和门槛都不能为空")

    seen_ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("每条评测用例必须是字典")
        case_id = case.get("case_id")
        score = case.get("top_score")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError("case_id 必须是唯一的非空字符串")
        if type(case.get("answerable")) is not bool:
            raise ValueError(f"{case_id}: answerable 必须是布尔值")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not -1 <= score <= 1):
            raise ValueError(f"{case_id}: top_score 必须是 -1 到 1 的有限数字")
        seen_ids.add(case_id)

    results = []
    for threshold in thresholds:
        if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold) or not -1 <= threshold <= 1):
            raise ValueError("门槛必须是 -1 到 1 的有限数字")
        counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
        for case in cases:
            admitted = case["top_score"] >= threshold
            label = ("tp" if admitted else "fn") if case["answerable"] else (
                "fp" if admitted else "tn"
            )
            counts[label] += 1
        results.append({"threshold": threshold, **counts})
    return results
