"""运行与规则开发集分离的证据评测；默认完全离线。"""

from __future__ import annotations

import argparse
import hashlib
import math
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic, sleep


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evidence_holdout_evaluation import evaluate_evidence_holdout  # noqa: E402
from evidence_review import ConfiguredEvidenceReviewer  # noqa: E402
from preview_tool_call import MODEL, API_URL  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测证据门禁在独立改写集上的表现")
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evaluation_data" / "evidence_holdout_cases.json",
    )
    parser.add_argument("--mode", choices=("rules", "live-review"), default="rules")
    parser.add_argument("--allow-external-model", action="store_true")
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=2.0,
        help="live-review 相邻模型请求的最小间隔，默认 2 秒",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--dataset-role", choices=("regression", "holdout"), default="regression",
        help="参与过修复的数据使用 regression；只有未参与开发的数据才标注 holdout",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "live-review" and not args.allow_external_model:
        raise SystemExit("live-review 会发送虚构问题和片段，必须显式添加 --allow-external-model")
    if not math.isfinite(args.request_interval_seconds) or args.request_interval_seconds < 0:
        raise SystemExit("--request-interval-seconds 不能为负数")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    output = args.output or (
        PROJECT_ROOT / "reports" / f"evidence-holdout-{args.mode}.json"
    )
    journal = output.with_suffix(".jsonl")
    if output.exists() or journal.exists():
        raise SystemExit("报告已存在，请用 --output 指定新文件以保留历史结果")
    output.parent.mkdir(parents=True, exist_ok=True)

    def save_result(result: dict) -> None:
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(f"{result['case_id']}: {result['outcome']}"
              f" error={result['error'] is not None}", flush=True)
    reviewer = None
    if args.mode == "live-review":
        configured_reviewer = ConfiguredEvidenceReviewer()
        last_request_at: float | None = None

        def paced_reviewer(question: str, candidates: list[dict]):
            nonlocal last_request_at
            if last_request_at is not None:
                remaining = args.request_interval_seconds - (monotonic() - last_request_at)
                if remaining > 0:
                    sleep(remaining)
            try:
                return configured_reviewer(question, candidates)
            finally:
                last_request_at = monotonic()

        reviewer = paced_reviewer
    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": args.mode,
            "dataset_role": args.dataset_role,
            "cases_file": args.cases.name,
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "model": MODEL if reviewer else None,
            "endpoint": API_URL if reviewer else None,
            "request_interval_seconds": args.request_interval_seconds,
            "source_sha256": {
                name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
                for name in ("evidence_support.py", "evidence_review.py",
                             "evidence_holdout_evaluation.py", "preview_tool_call.py",
                             "retry_policy.py")
            },
            "notes": [
                ("该数据集用于回归；通过率不能解释为未见问题的泛化准确率。"
                 if args.dataset_role == "regression" else
                 "调用方声明该数据集未参与当前实现开发；须保留冻结来源与时间。"),
                "全部问题和政策片段均为虚构数据，不含个人信息。",
                "rules 模式不调用外部模型；live-review 才调用本地配置的真实模型。",
                f"live-review 相邻请求至少间隔 {args.request_interval_seconds} 秒。",
            ],
        },
        "evaluation": evaluate_evidence_holdout(
            cases, reviewer, capture_review_errors=True, on_result=save_result
        ),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report["evaluation"].items()
                      if key != "results"}, ensure_ascii=False, indent=2))
    print(f"报告已写入: {output.resolve()}")
    counts = report["evaluation"]["counts"]
    return 0 if counts["fn"] == 0 and counts["fp"] == 0 and not report["evaluation"]["review_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
