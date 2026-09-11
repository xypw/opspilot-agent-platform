"""比较两份 Agent 评测报告，并用退出码控制 CI 门禁。"""

import argparse
from pathlib import Path
import sys


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation_regression import compare_evaluation_reports  # noqa: E402
from evaluation_report import load_evaluation_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 Agent 评测是否发生能力回归")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        baseline = load_evaluation_report(args.baseline)
        current = load_evaluation_report(args.current)
        regression = compare_evaluation_reports(baseline, current)
    except (OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    print(regression.model_dump_json(indent=2))
    return 0 if regression.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
