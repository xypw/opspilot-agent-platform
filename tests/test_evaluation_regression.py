"""Agent 回归检查测试：总体分数不变时也能发现关键用例退化。"""

import unittest

from agent_evaluation import AgentEvaluationResult, build_evaluation_summary
from evaluation_regression import compare_evaluation_reports, find_regressed_case_ids
from evaluation_report import AgentEvaluationReport


def build_report(first_success: bool, second_success: bool) -> AgentEvaluationReport:
    results = [
        AgentEvaluationResult(case_id="case-a", success=first_success),
        AgentEvaluationResult(case_id="case-b", success=second_success),
    ]
    return AgentEvaluationReport(
        mode="mock",
        runtime="isolated",
        cases_file="evaluation_data/cases.json",
        cases_sha256="a" * 64,
        summary=build_evaluation_summary(results),
    )


class EvaluationRegressionTests(unittest.TestCase):
    def test_finds_case_regression_even_when_overall_rate_is_unchanged(self):
        baseline = build_report(first_success=True, second_success=False)
        current = build_report(first_success=False, second_success=True)

        self.assertEqual(
            baseline.summary.task_success_rate,
            current.summary.task_success_rate,
        )
        self.assertEqual(find_regressed_case_ids(baseline, current), ["case-a"])

    def test_rejects_different_evaluation_conditions(self):
        baseline = build_report(first_success=True, second_success=True)
        current = baseline.model_copy(update={"mode": "live"})

        with self.assertRaisesRegex(ValueError, "运行条件"):
            find_regressed_case_ids(baseline, current)

    def test_comparison_passes_when_no_successful_case_regresses(self):
        baseline = build_report(first_success=True, second_success=False)
        current = build_report(first_success=True, second_success=True)

        regression = compare_evaluation_reports(baseline, current)

        self.assertTrue(regression.passed)
        self.assertEqual(regression.regressed_case_ids, [])
        self.assertEqual(regression.baseline_task_success_rate, 0.5)
        self.assertEqual(regression.current_task_success_rate, 1.0)

    def test_duplicate_case_ids_are_not_comparable(self):
        baseline = build_report(first_success=True, second_success=True)
        current = baseline.model_copy(deep=True)
        current.summary.results[1].case_id = "case-a"

        with self.assertRaisesRegex(ValueError, "用例集合"):
            find_regressed_case_ids(baseline, current)


if __name__ == "__main__":
    unittest.main()
