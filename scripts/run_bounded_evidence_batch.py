"""两组独立计分的有界真实证据评测；执行前冻结来源，逐例落盘，不覆盖历史。"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evidence_review
import preview_tool_call
from evidence_holdout_evaluation import evaluate_evidence_holdout

MAX_CASES = 40
MAX_HTTP_ATTEMPTS = 120
PER_REVIEW_HTTP_ATTEMPTS = 3
FIXED_MODEL = "glm-4.7-flash"
FIXED_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
SOURCE_FILES = (
    "scripts/run_bounded_evidence_batch.py", "evidence_review.py", "evidence_support.py",
    "evidence_holdout_evaluation.py", "retry_policy.py", "preview_tool_call.py",
    "model_rate_limit.py", "tool_schema.py",
)


def check_configuration() -> None:
    if (preview_tool_call.MODEL != FIXED_MODEL
            or preview_tool_call.API_URL != FIXED_ENDPOINT
            or evidence_review.MAX_REVIEW_HTTP_ATTEMPTS != PER_REVIEW_HTTP_ATTEMPTS):
        raise ValueError("模型、endpoint 或单例 HTTP 上限发生变化，拒绝运行此授权批次")


def load_groups(regression_path: Path, holdout_path: Path) -> tuple[dict, dict]:
    paths = {"regression": Path(regression_path).resolve(strict=True),
             "holdout": Path(holdout_path).resolve(strict=True)}
    if paths["regression"].samefile(paths["holdout"]):
        raise ValueError("两组不能指向同一数据文件或其路径别名")
    groups, datasets = {}, {}
    seen_ids, seen_inputs = set(), set()
    for role, path in paths.items():
        raw = path.read_bytes()
        cases = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"{role}: 数据集必须是非空 JSON 数组")
        for case in cases:
            if not isinstance(case, dict) or not {
                "case_id", "question", "candidate", "sufficient"
            }.issubset(case) or set(case) - {
                "case_id", "question", "candidate", "sufficient", "rationale"
            }:
                raise ValueError(f"{role}: 案例字段不符合 schema")
            case_id, question, candidate = case["case_id"], case["question"], case["candidate"]
            if (not isinstance(case_id, str) or not case_id.strip()
                    or case_id != case_id.strip() or case_id in seen_ids):
                raise ValueError("case_id 必须在两组中全局唯一，且不含首尾空白")
            if not isinstance(question, str) or not question.strip() or len(question) > 6000:
                raise ValueError(f"{case_id}: question 必须是 1 至 6000 字符的非空字符串")
            if type(case["sufficient"]) is not bool or (
                "rationale" in case and not isinstance(case["rationale"], str)
            ):
                raise ValueError(f"{case_id}: 标签或 rationale 类型无效")
            if (not isinstance(candidate, dict)
                    or not {"chunk_id", "content"}.issubset(candidate)
                    or set(candidate) - {"chunk_id", "content", "title", "page", "score"}):
                raise ValueError(f"{case_id}: candidate 字段不符合 schema")
            # 复用真实审查的输入校验，但不会载入密钥或发送请求。
            evidence_review.build_evidence_review_messages(question, [candidate])
            if ("title" in candidate and not isinstance(candidate["title"], str)) or (
                "page" in candidate and (type(candidate["page"]) is not int or candidate["page"] < 1)
            ) or ("score" in candidate and (
                type(candidate["score"]) not in {int, float} or not math.isfinite(candidate["score"])
            )):
                raise ValueError(f"{case_id}: candidate 元数据类型无效")
            identity = (" ".join(question.split()), " ".join(candidate["content"].split()))
            if identity in seen_inputs:
                raise ValueError("发现重复问题与候选原文，不能通过改 ID 混入另一组")
            seen_ids.add(case_id)
            seen_inputs.add(identity)
        groups[role] = cases
        datasets[role] = {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                          "case_count": len(cases), "case_ids": [c["case_id"] for c in cases]}
    if sum(map(len, groups.values())) > MAX_CASES:
        raise ValueError(f"两组合计最多 {MAX_CASES} 例")
    return groups, datasets


def summarize(results: list[dict]) -> dict:
    valid = [r for r in results if r["status"] == "evaluated"]
    counts = {key: sum(r["outcome"] == key for r in valid) for key in ("tp", "fn", "fp", "tn")}
    positive = counts["tp"] + counts["fn"]
    negative = counts["tn"] + counts["fp"]
    correct = counts["tp"] + counts["tn"]
    not_run = [r["case_id"] for r in results if r["status"] == "not_run"]
    return {
        "planned_cases": len(results), "attempted_cases": len(results) - len(not_run),
        "evaluated_cases": len(valid), "run_errors": sum(r["status"] == "run_error" for r in results),
        "not_run_cases": len(not_run), "not_run_case_ids": not_run,
        "counts": counts, "valid_correct_cases": correct,
        "correct_over_planned": round(correct / len(results), 4) if results else None,
        "accuracy_on_valid_cases": round(correct / len(valid), 4) if valid else None,
        "false_rejection_rate_on_valid_cases": round(counts["fn"] / positive, 4) if positive else None,
        "false_admission_rate_on_valid_cases": round(counts["fp"] / negative, 4) if negative else None,
        "model_http_attempts": sum(r["model_http_attempts"] for r in results),
        "all_passed": bool(results) and correct == len(results),
    }


def write_json(stream, value: dict, *, line: bool = False) -> None:
    json.dump(value, stream, ensure_ascii=False, indent=None if line else 2, allow_nan=False)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def run_batch(regression_cases: Path, holdout_cases: Path, output: Path, *, reviewer,
              max_http_attempts: int = MAX_HTTP_ATTEMPTS, request_interval_seconds: float = 5.0,
              pause=sleep) -> dict:
    check_configuration()
    if type(max_http_attempts) is not int or not 1 <= max_http_attempts <= MAX_HTTP_ATTEMPTS:
        raise ValueError("全局 HTTP 预算必须在 1 至 120 之间")
    if not math.isfinite(request_interval_seconds) or request_interval_seconds < 0:
        raise ValueError("请求间隔必须是有限非负数")
    groups, datasets = load_groups(regression_cases, holdout_cases)
    output = Path(output).resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("--output 必须使用新的 .json 路径")
    journal, manifest = output.with_suffix(".jsonl"), output.with_suffix(".manifest.json")
    if any(path.exists() for path in (output, journal, manifest)):
        raise ValueError("报告、逐例 journal 或冻结 manifest 已存在，禁止覆盖或续跑")
    metadata = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "mode": "bounded-live-review", "model": FIXED_MODEL, "endpoint": FIXED_ENDPOINT,
        "max_cases": MAX_CASES, "max_http_attempts": max_http_attempts,
        "per_review_http_attempts": PER_REVIEW_HTTP_ATTEMPTS,
        "request_interval_seconds": request_interval_seconds, "datasets": datasets,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in SOURCE_FILES},
        "notes": ["regression 与 holdout 分开计分；运行错误和未运行不算正确。",
                  "只外发虚构问题、候选 chunk_id/content 和审查提示；不外发标签或源码。",
                  "holdout 角色由调用方声明；本入口不能证明未参与开发。"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    results, used, stop_reason = [], 0, None
    with ExitStack() as stack:
        # x 模式同时保护并发启动；manifest 在首次模型调用前持久化。
        report_stream = stack.enter_context(output.open("x", encoding="utf-8"))
        journal_stream = stack.enter_context(journal.open("x", encoding="utf-8"))
        manifest_stream = stack.enter_context(manifest.open("x", encoding="utf-8"))
        write_json(manifest_stream, metadata)
        for role, cases in groups.items():
            for case in cases:
                if stop_reason is None and max_http_attempts - used < PER_REVIEW_HTTP_ATTEMPTS:
                    stop_reason = "http_budget_insufficient_for_next_case"
                if stop_reason is not None:
                    result = {"case_id": case["case_id"], "dataset_role": role,
                              "status": "not_run", "outcome": "not_run", "reason": stop_reason,
                              "expected_sufficient": case["sufficient"], "predicted_sufficient": None,
                              "error": None, "model_http_attempts": 0}
                else:
                    if results and results[-1]["model_http_attempts"]:
                        pause(request_interval_seconds)
                    check_configuration()
                    result = evaluate_evidence_holdout(
                        [case], reviewer, capture_review_errors=True,
                    )["results"][0]
                    attempts = result["model_http_attempts"]
                    if type(attempts) is not int or not 0 <= attempts <= PER_REVIEW_HTTP_ATTEMPTS:
                        raise ValueError("审查器 HTTP 计数违反已冻结的单例上限，立即终止")
                    used += attempts
                    result["dataset_role"] = role
                    result["status"] = "run_error" if result["error"] else "evaluated"
                    if result["error"]:
                        # 评测器的安全拒答不能冒充一个语义正确的 tn。
                        result["outcome"] = "run_error"
                        if result["error"].get("status_code") == 429:
                            stop_reason = "provider_429_after_retries"
                    result["cumulative_model_http_attempts"] = used
                results.append(result)
                write_json(journal_stream, result, line=True)
        by_group = {role: summarize([r for r in results if r["dataset_role"] == role]) for role in groups}
        report = {
            "metadata": metadata, "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "planned_cases": len(results), "model_http_attempts": used,
                "remaining_http_attempts": max_http_attempts - used, "stop_reason": stop_reason,
                "run_errors": sum(g["run_errors"] for g in by_group.values()),
                "not_run_cases": sum(g["not_run_cases"] for g in by_group.values()),
                "all_passed": all(g["all_passed"] for g in by_group.values()),
            },
            "groups": by_group, "results": results,
        }
        write_json(report_stream, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression-cases", type=Path, required=True)
    parser.add_argument("--holdout-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-external-model", action="store_true", required=True)
    parser.add_argument("--max-http-attempts", type=int, default=MAX_HTTP_ATTEMPTS)
    parser.add_argument("--request-interval-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    report = run_batch(
        args.regression_cases, args.holdout_cases, args.output,
        reviewer=evidence_review.ConfiguredEvidenceReviewer(),
        max_http_attempts=args.max_http_attempts,
        request_interval_seconds=args.request_interval_seconds,
    )
    print(json.dumps({"summary": report["summary"], "groups": report["groups"]},
                     ensure_ascii=False, indent=2))
    return 0 if report["summary"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
