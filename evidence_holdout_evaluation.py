"""独立证据集评测：比较确定性门禁与三态门禁加语义审查。"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
import httpx
from preview_tool_call import ModelAPIError
from evidence_review import EvidenceReviewer, EvidenceReviewReply, verify_evidence_review
from evidence_support import (
    assess_evidence_for_question,
    filter_evidence_for_question,
    partition_evidence_for_question,
)


def evaluate_evidence_holdout(
    cases: list[dict], reviewer: EvidenceReviewer | None = None,
    *, capture_review_errors: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> dict:
    """评测单候选证据；reviewer=None 时复现纯规则基线。"""
    if not isinstance(cases, list) or not cases:
        raise ValueError("独立证据评测集必须是非空列表")

    counts = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    uncertain_cases = 0
    reviewer_calls = 0
    model_http_attempts = 0
    seen_ids = set()
    results = []

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("评测案例必须是字典")
        case_id = case.get("case_id")
        question = case.get("question")
        candidate = case.get("candidate")
        expected = case.get("sufficient")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError("case_id 必须是唯一的非空字符串")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{case_id}: question 必须是非空字符串")
        if not isinstance(candidate, dict):
            raise ValueError(f"{case_id}: candidate 必须是字典")
        if type(expected) is not bool:
            raise ValueError(f"{case_id}: sufficient 必须是布尔值")

        candidates = [candidate]
        assessment = assess_evidence_for_question(question, str(candidate.get("content", "")))
        partition = partition_evidence_for_question(question, candidates)
        decision = (
            "admit" if partition["admitted"] else
            "uncertain" if partition["uncertain"] else "reject"
        )
        uncertain_cases += int(decision == "uncertain")
        raw_verdict = None
        error_info = None
        attempts = 0
        duration_ms = 0.0

        if reviewer is None:
            predicted = bool(filter_evidence_for_question(question, candidates))
            selected = candidates if predicted else []
        else:
            reviewable = partition["admitted"] + partition["uncertain"]
            if not reviewable:
                predicted = False
                selected = []
            else:
                reviewer_calls += 1
                try:
                    reply = reviewer(question, deepcopy(reviewable))
                    if isinstance(reply, EvidenceReviewReply):
                        raw_verdict = reply.verdict
                        attempts = reply.telemetry.http_attempts
                        duration_ms = reply.telemetry.duration_ms
                    else:
                        raw_verdict = reply
                    verdict, selected = verify_evidence_review(raw_verdict, reviewable)
                    predicted = verdict.supported
                except (ValueError, httpx.HTTPError, OSError) as error:
                    if not capture_review_errors:
                        raise
                    attempts = attempts or getattr(error, "model_http_attempts", 0)
                    duration_ms = duration_ms or sum(getattr(error, "model_turn_durations_ms", []))
                    error_info = {"type": type(error).__name__}
                    if isinstance(error, ModelAPIError):
                        error_info["status_code"] = error.status_code
                        error_info["provider_error_code"] = error.provider_code
                    # 审查失败时不放行，但需单独统计为运行失败，不能冒充正确拒答。
                    predicted, selected = False, []
                model_http_attempts += attempts

        outcome = ("tp" if predicted else "fn") if expected else (
            "fp" if predicted else "tn"
        )
        counts[outcome] += 1
        result = {
            "case_id": case_id,
            "expected_sufficient": expected,
            "gate_decision": decision,
            "gate_reason": assessment.reason,
            "required_answer_types": list(assessment.required_types),
            "provided_answer_types": list(assessment.provided_types),
            "predicted_sufficient": predicted,
            "outcome": outcome,
            "selected_chunk_ids": [item["chunk_id"] for item in selected],
            "review_verdict": raw_verdict,
            "error": error_info,
            "model_http_attempts": attempts,
            "duration_ms": duration_ms,
        }
        results.append(result)
        if on_result is not None:
            on_result(deepcopy(result))
        seen_ids.add(case_id)

    total = len(cases)
    positive = counts["tp"] + counts["fn"]
    negative = counts["tn"] + counts["fp"]
    return {
        "case_count": total,
        "positive_cases": positive,
        "negative_cases": negative,
        "counts": counts,
        "false_rejection_rate": round(counts["fn"] / positive, 4) if positive else 0.0,
        "false_admission_rate": round(counts["fp"] / negative, 4) if negative else 0.0,
        "uncertain_cases": uncertain_cases,
        "reviewer_calls": reviewer_calls,
        "model_http_attempts": model_http_attempts,
        "review_errors": sum(item["error"] is not None for item in results),
        "valid_correct_cases": sum(
            item["error"] is None and item["outcome"] in {"tp", "tn"}
            for item in results
        ),
        "results": results,
    }
