"""Agent 评测命令测试：报告落盘和 CI 退出码。"""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evaluation import AgentEvaluationResult, build_evaluation_summary
from scripts.run_agent_evaluation import main


class RunAgentEvaluationScriptTests(unittest.TestCase):
    def test_main_saves_report_with_run_metadata(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "nested" / "report.json"
            arguments = [
                "run_agent_evaluation.py",
                "--mode", "mock",
                "--runtime", "isolated",
                "--output", str(output_path),
            ]

            with patch.object(sys, "argv", arguments), redirect_stdout(StringIO()):
                exit_code = main()

            saved_data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(saved_data["mode"], "mock")
            self.assertEqual(saved_data["runtime"], "isolated")
            self.assertEqual(
                saved_data["cases_file"],
                "evaluation_data/agent_task_cases.json",
            )
            self.assertRegex(saved_data["cases_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(saved_data["summary"]["successful_cases"], 7)

    @patch("scripts.run_agent_evaluation.run_evaluation_cases")
    def test_runner_error_returns_nonzero_exit_code(self, mocked_run_cases):
        mocked_run_cases.return_value = build_evaluation_summary([
            AgentEvaluationResult(
                case_id="upstream-error",
                success=False,
                failure_reasons=["runner_error"],
                error_type="TimeoutError",
            )
        ])
        arguments = [
            "run_agent_evaluation.py",
            "--mode", "mock",
            "--runtime", "isolated",
        ]

        with patch.object(sys, "argv", arguments), redirect_stdout(StringIO()):
            exit_code = main()

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
