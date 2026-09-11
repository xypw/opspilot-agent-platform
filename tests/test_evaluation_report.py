"""评测报告测试：元数据完整，并能写入尚不存在的输出目录。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evaluation import build_evaluation_summary
from evaluation_report import AgentEvaluationReport, save_evaluation_report


class AgentEvaluationReportTests(unittest.TestCase):
    def test_saves_versioned_utf8_json_report(self):
        report = AgentEvaluationReport(
            mode="mock",
            runtime="isolated",
            cases_file="evaluation_data/agent_task_cases.json",
            summary=build_evaluation_summary([]),
        )

        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "reports" / "agent-evaluation.json"

            saved_path = save_evaluation_report(report, output_path)

            self.assertEqual(saved_path, output_path)
            saved_data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_data["schema_version"], "1.0")
            self.assertEqual(saved_data["mode"], "mock")
            self.assertEqual(saved_data["runtime"], "isolated")
            self.assertEqual(saved_data["summary"]["total_cases"], 0)
            self.assertTrue(saved_data["generated_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
