"""限定四条历史虚构案例复测；供应商持续 429 时立即停止后续模型调用。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence_holdout_evaluation import evaluate_evidence_holdout
from evidence_review import ConfiguredEvidenceReviewer
from preview_tool_call import API_URL, MODEL

CASE_IDS = ("h15_return_from_warranty", "h06_high_ticket_synonym",
            "h09_invoice_process_paraphrase", "h17_process_from_status")


def run_cases(cases, reviewer, *, pause=sleep):
    results = []
    for index, case in enumerate(cases):
        if index and results[-1]["model_http_attempts"]:
            pause(5)
        result = evaluate_evidence_holdout([case], reviewer, capture_review_errors=True)["results"][0]
        results.append(result)
        # HTTP 429 已经过请求层有限重试，不能继续批量冲击同一账户。
        if (result.get("error") or {}).get("status_code") == 429:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-external-model", action="store_true", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", action="append", choices=CASE_IDS,
                        help="仅复测指定历史案例；可重复传入不同编号，默认四例")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("报告已存在，必须使用新路径，不能覆盖历史结果")
    dataset = ROOT / "evaluation_data/evidence_holdout_cases.json"
    by_id = {c["case_id"]: c for c in json.loads(dataset.read_text(encoding="utf-8"))}
    selected_ids = args.case_id or list(CASE_IDS)
    if len(set(selected_ids)) != len(selected_ids):
        parser.error("案例编号不得重复，避免意外消耗模型请求")
    cases = [by_id[key] for key in selected_ids]
    results = run_cases(cases, ConfiguredEvidenceReviewer())
    completed_ids = {r["case_id"] for r in results}
    report = {
        "metadata": {"generated_at": datetime.now(timezone.utc).isoformat(),
                     "mode": "live-review-failure-recheck", "dataset_role": "regression",
                     "model": MODEL, "endpoint": API_URL,
                     "cases_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                     "planned_case_ids": selected_ids,
                     "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                       for name in ("evidence_review.py", "evidence_support.py",
                                                    "evidence_holdout_evaluation.py", "retry_policy.py",
                                                    "preview_tool_call.py", "model_rate_limit.py")}},
        "summary": {"planned_cases": len(cases), "completed_cases": len(results),
                    "not_run_case_ids": [key for key in selected_ids if key not in completed_ids],
                    "model_http_attempts": sum(r["model_http_attempts"] for r in results),
                    "review_errors": sum(r["error"] is not None for r in results),
                    "valid_correct_cases": sum(r["error"] is None and r["outcome"] in {"tp", "tn"} for r in results),
                    "stopped_on_429": any((r["error"] or {}).get("status_code") == 429 for r in results)},
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["valid_correct_cases"] == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
