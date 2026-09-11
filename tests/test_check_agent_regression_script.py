"""回归门禁命令测试：通过、回归和不可比较使用不同退出码。"""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evaluation import AgentEvaluationResult, build_evaluation_summary
from evaluation_report import AgentEvaluationReport, save_evaluation_report
from scripts.check_agent_regression import main


def save_report(path: Path, success: bool, *, mode: str = "mock") -> None:
    result = AgentEvaluationResult(case_id="case-a", success=success)
    report = AgentEvaluationReport(
        mode=mode,
        runtime="isolated",
        cases_file="evaluation_data/cases.json",
        cases_sha256="a" * 64,
        summary=build_evaluation_summary([result]),
    )
    save_evaluation_report(report, path)


class CheckAgentRegressionScriptTests(unittest.TestCase):
    def run_main(self, baseline: Path, current: Path) -> int:
        arguments = [
            "check_agent_regression.py",
            "--baseline", str(baseline),
            "--current", str(current),
        ]
        with (
            patch.object(sys, "argv", arguments),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            return main()

    def test_returns_zero_without_regression(self):
        with TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            current = Path(directory) / "current.json"
            save_report(baseline, True)
            save_report(current, True)

            self.assertEqual(self.run_main(baseline, current), 0)

    def test_returns_one_when_a_case_regresses(self):
        with TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            current = Path(directory) / "current.json"
            save_report(baseline, True)
            save_report(current, False)

            self.assertEqual(self.run_main(baseline, current), 1)

    def test_returns_two_when_reports_are_not_comparable(self):
        with TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            current = Path(directory) / "current.json"
            save_report(baseline, True, mode="mock")
            save_report(current, True, mode="live")

            self.assertEqual(self.run_main(baseline, current), 2)


if __name__ == "__main__":
    unittest.main()
