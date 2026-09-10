"""多步 Agent 任务成功判定测试。"""

import unittest

from agent_evaluation import (
    AgentEvaluationCase,
    AgentEvaluationResult,
    build_evaluation_summary,
    collect_failure_reasons,
    evaluate_response,
    is_task_successful,
)
from agent_graph import AgentGraphResponse


def build_response(*, tools: list[str], answer: str, status: str = "COMPLETED") -> AgentGraphResponse:
    """构造最小公开响应，避免单元测试启动模型或真实服务。"""
    return AgentGraphResponse(
        thread_id="evaluation-thread",
        mode="mock",
        status=status,
        answer=answer,
        tool_name=tools[-1] if tools else "no_tool",
        tool_result=None,
        tool_steps=len(tools),
        tool_trace=[
            {"step": index, "tool_name": tool_name, "result": {}}
            for index, tool_name in enumerate(tools, start=1)
        ],
        model_requests=0,
        simulated_model_requests=len(tools) + 1,
    )


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = AgentEvaluationCase(
            case_id="order-and-refund-policy",
            question="查询订单 O-2001，并告诉我退款多久到账",
            expected_tools=["query_order", "search_knowledge_base"],
            answer_must_contain=["机械键盘", "三个工作日"],
            citation_required=True,
        )

    def test_all_conditions_met_is_success(self):
        response = build_response(
            tools=["query_order", "search_knowledge_base"],
            answer="商品是机械键盘，退款三个工作日到账。\n\n来源：《售后与退款制度》第2页",
        )
        self.assertTrue(is_task_successful(self.case, response))

    def test_plausible_answer_with_wrong_tool_sequence_is_failure(self):
        response = build_response(
            tools=["search_knowledge_base", "query_order"],
            answer="商品是机械键盘，退款三个工作日到账。\n\n来源：《售后与退款制度》第2页",
        )
        self.assertFalse(is_task_successful(self.case, response))

    def test_missing_required_citation_is_failure(self):
        response = build_response(
            tools=["query_order", "search_knowledge_base"],
            answer="商品是机械键盘，退款三个工作日到账。",
        )
        self.assertFalse(is_task_successful(self.case, response))

    def test_successful_response_has_no_failure_reasons(self):
        response = build_response(
            tools=["query_order", "search_knowledge_base"],
            answer="商品是机械键盘，退款三个工作日到账。\n\n来源：《售后与退款制度》第2页",
        )

        self.assertEqual(collect_failure_reasons(self.case, response), [])

    def test_all_failure_reasons_are_collected(self):
        response = build_response(
            tools=["search_knowledge_base", "query_order"],
            answer="没有包含评测要求的事实和引用。",
            status="WAITING_CONFIRMATION",
        )

        self.assertEqual(
            collect_failure_reasons(self.case, response),
            [
                "status_mismatch",
                "tool_sequence_mismatch",
                "missing_required_text",
                "missing_citation",
            ],
        )

    def test_evaluate_response_builds_structured_result(self):
        response = build_response(
            tools=["query_order", "search_knowledge_base"],
            answer="商品是机械键盘，退款三个工作日到账。",
        )

        result = evaluate_response(self.case, response)

        self.assertEqual(result.case_id, "order-and-refund-policy")
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reasons, ["missing_citation"])

    def test_summary_counts_failed_cases_and_failure_reasons_separately(self):
        results = [
            AgentEvaluationResult(case_id="case-1", success=True),
            AgentEvaluationResult(
                case_id="case-2",
                success=False,
                failure_reasons=["tool_sequence_mismatch", "missing_citation"],
            ),
            AgentEvaluationResult(
                case_id="case-3",
                success=False,
                failure_reasons=["missing_required_text"],
            ),
        ]

        summary = build_evaluation_summary(results)

        self.assertEqual(summary.total_cases, 3)
        self.assertEqual(summary.successful_cases, 1)
        self.assertEqual(summary.failed_cases, 2)
        self.assertAlmostEqual(summary.task_success_rate, 1 / 3)
        self.assertEqual(
            summary.failure_counts,
            {
                "tool_sequence_mismatch": 1,
                "missing_citation": 1,
                "missing_required_text": 1,
            },
        )

    def test_empty_results_have_zero_success_rate(self):
        summary = build_evaluation_summary([])

        self.assertEqual(summary.total_cases, 0)
        self.assertEqual(summary.successful_cases, 0)
        self.assertEqual(summary.failed_cases, 0)
        self.assertEqual(summary.task_success_rate, 0.0)
        self.assertEqual(summary.failure_counts, {})
        self.assertEqual(summary.results, [])


if __name__ == "__main__":
    unittest.main()
