"""固定 Agent 评测集的结构校验；不调用模型、数据库或 HTTP。"""

import json
from pathlib import Path
import unittest

from agent_evaluation import AgentEvaluationCase


CASES_FILE = Path(__file__).parents[1] / "evaluation_data" / "agent_task_cases.json"


class AgentEvaluationDataTests(unittest.TestCase):
    def test_cases_are_valid_and_ids_are_unique(self):
        raw_cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
        cases = [AgentEvaluationCase.model_validate(item) for item in raw_cases]

        self.assertEqual(len(cases), 7)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        self.assertIn("COMPLETED", {case.expected_status for case in cases})
        self.assertIn("WAITING_CONFIRMATION", {case.expected_status for case in cases})
        injection_case = next(
            case
            for case in cases
            if case.case_id == "prompt-injection-cannot-bypass-return-confirmation"
        )
        self.assertFalse(injection_case.return_application_expected)
        unsupported = next(case for case in cases if case.case_id == "refund-how-to-unsupported")
        self.assertIn("来源：", unsupported.answer_must_not_contain)


if __name__ == "__main__":
    unittest.main()
