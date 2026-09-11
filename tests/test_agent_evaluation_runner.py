"""Agent 批量评测执行器测试：加载、隔离、异常和 LangGraph 适配。"""

import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evaluation import AgentEvaluationCase
from agent_evaluation_runner import (
    build_langgraph_runner,
    load_evaluation_cases,
    require_external_model_permission,
    run_evaluation_cases,
    select_evaluation_cases,
)
from agent_evaluation_runtime import build_isolated_evaluation_graph
from agent_graph import AgentGraphResponse


CASES_FILE = Path(__file__).parents[1] / "evaluation_data" / "agent_task_cases.json"


def build_response(case_id: str, *, tools: list[str], answer: str) -> AgentGraphResponse:
    """构造一个不依赖模型、HTTP 或数据库的公开 Agent 响应。"""
    return AgentGraphResponse(
        thread_id=f"eval-{case_id}",
        mode="mock",
        status="COMPLETED",
        answer=answer,
        tool_name=tools[-1],
        tool_result=None,
        tool_steps=len(tools),
        tool_trace=[
            {"step": index, "tool_name": tool_name, "result": {}}
            for index, tool_name in enumerate(tools, start=1)
        ],
        model_requests=0,
        simulated_model_requests=len(tools) + 1,
    )


class AgentEvaluationRunnerTests(unittest.TestCase):
    def test_loads_the_fixed_json_cases(self):
        cases = load_evaluation_cases(CASES_FILE)

        self.assertEqual(len(cases), 5)
        self.assertEqual(cases[0].case_id, "ticket-status-lookup")

    def test_duplicate_case_ids_are_rejected_before_execution(self):
        duplicated = [{
            "case_id": "same-id",
            "question": "查询工单 T-1003",
            "expected_tools": ["query_ticket"],
        }] * 2
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(duplicated), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "case_id 不能重复"):
                load_evaluation_cases(path)

    def test_runner_receives_only_case_id_and_question(self):
        case = AgentEvaluationCase(
            case_id="ticket-case",
            question="查询工单 T-1003",
            expected_tools=["query_ticket"],
            answer_must_contain=["T-1003"],
        )
        received_arguments: list[tuple[str, str]] = []

        def fake_runner(case_id: str, question: str) -> AgentGraphResponse:
            received_arguments.append((case_id, question))
            return build_response(case_id, tools=["query_ticket"], answer="工单 T-1003")

        summary = run_evaluation_cases([case], fake_runner)

        self.assertEqual(received_arguments, [("ticket-case", "查询工单 T-1003")])
        self.assertEqual(summary.task_success_rate, 1.0)
        self.assertGreaterEqual(summary.results[0].duration_ms, 0.0)
        self.assertEqual(summary.results[0].actual_tools, ["query_ticket"])

    def test_one_runner_error_does_not_stop_later_cases(self):
        cases = [
            AgentEvaluationCase(case_id="broken", question="第一个问题", expected_tools=["query_ticket"]),
            AgentEvaluationCase(case_id="healthy", question="第二个问题", expected_tools=["query_ticket"]),
        ]
        executed_ids: list[str] = []

        def flaky_runner(case_id: str, question: str) -> AgentGraphResponse:
            executed_ids.append(case_id)
            if case_id == "broken":
                raise TimeoutError("测试异常消息不应进入报告")
            return build_response(case_id, tools=["query_ticket"], answer="查询完成")

        summary = run_evaluation_cases(cases, flaky_runner)

        self.assertEqual(executed_ids, ["broken", "healthy"])
        self.assertEqual(summary.successful_cases, 1)
        self.assertEqual(summary.failed_cases, 0)
        self.assertEqual(summary.errored_cases, 1)
        self.assertEqual(summary.task_success_rate, 0.5)
        self.assertEqual(summary.evaluation_completion_rate, 0.5)
        self.assertEqual(summary.scored_success_rate, 1.0)
        self.assertEqual(summary.failure_counts, {"runner_error": 1})
        self.assertEqual(summary.results[0].error_type, "TimeoutError")
        self.assertIsNone(summary.results[0].actual_answer)

    def test_safe_provider_error_codes_are_kept_without_error_message(self):
        case = AgentEvaluationCase(
            case_id="provider-error",
            question="测试上游错误",
            expected_tools=["query_ticket"],
        )

        class SafeProviderError(Exception):
            status_code = 429
            provider_code = "1305"
            model_http_attempts = 3
            model_retry_count = 2
            model_turn_durations_ms = [46000.0]
            model_http_attempt_durations_ms = [15000.0, 15000.0, 15400.0]

        def failing_runner(case_id: str, question: str) -> AgentGraphResponse:
            raise SafeProviderError("这段原始错误消息不能进入评测报告")

        result = run_evaluation_cases([case], failing_runner).results[0]

        self.assertEqual(result.error_status_code, 429)
        self.assertEqual(result.provider_error_code, "1305")
        self.assertEqual(result.model_http_attempts, 3)
        self.assertEqual(result.model_retry_count, 2)
        self.assertEqual(result.model_turn_durations_ms, [46000.0])
        self.assertEqual(len(result.model_http_attempt_durations_ms), 3)
        self.assertNotIn("原始错误消息", result.model_dump_json())

    @patch("agent_evaluation_runner.start_agent_graph")
    def test_failed_turn_merges_previous_checkpoint_telemetry(self, mocked_start):
        case = AgentEvaluationCase(
            case_id="multi-step-error",
            question="先查询订单，再查询退款政策",
            expected_tools=["query_order", "search_knowledge_base"],
        )

        class FinalTurnError(Exception):
            model_http_attempts = 3
            model_retry_count = 2
            model_turn_durations_ms = [46000.0]
            model_http_attempt_durations_ms = [15000.0, 15000.0, 15400.0]

        class CheckpointGraph:
            def get_state(self, config):
                self.received_config = config
                return SimpleNamespace(values={
                    "model_requests": 1,
                    "simulated_model_requests": 0,
                    "model_http_attempts": 1,
                    "model_retry_count": 0,
                    "model_turn_durations_ms": [18000.0],
                    "model_http_attempt_durations_ms": [18000.0],
                })

        graph = CheckpointGraph()
        mocked_start.side_effect = FinalTurnError("第二轮模型失败")
        runner = build_langgraph_runner(
            graph,
            mode="live",
            thread_id_factory=lambda case_id: f"fixed-{case_id}",
        )

        result = run_evaluation_cases([case], runner).results[0]

        self.assertEqual(
            graph.received_config,
            {"configurable": {"thread_id": "fixed-multi-step-error"}},
        )
        self.assertEqual(result.model_requests, 1)
        self.assertEqual(result.model_http_attempts, 4)
        self.assertEqual(result.model_retry_count, 2)
        self.assertEqual(result.model_turn_durations_ms, [18000.0, 46000.0])
        self.assertEqual(len(result.model_http_attempt_durations_ms), 4)
        self.assertEqual(result.error_type, "FinalTurnError")

    @patch("agent_evaluation_runner.start_agent_graph")
    def test_checkpoint_read_failure_preserves_original_error(self, mocked_start):
        case = AgentEvaluationCase(
            case_id="checkpoint-error",
            question="测试双重故障",
            expected_tools=["query_ticket"],
        )

        class BrokenCheckpointGraph:
            def get_state(self, config):
                raise ConnectionError("Checkpoint 不可用")

        mocked_start.side_effect = TimeoutError("原始模型超时")
        runner = build_langgraph_runner(BrokenCheckpointGraph(), mode="live")

        result = run_evaluation_cases([case], runner).results[0]

        self.assertEqual(result.error_type, "TimeoutError")
        self.assertEqual(result.failure_reasons, ["runner_error"])

    def test_selected_cases_keep_requested_order(self):
        cases = load_evaluation_cases(CASES_FILE)

        selected = select_evaluation_cases(
            cases,
            ["order-and-refund-policy", "ticket-status-lookup"],
        )

        self.assertEqual(
            [case.case_id for case in selected],
            ["order-and-refund-policy", "ticket-status-lookup"],
        )

    def test_unknown_selected_case_is_rejected(self):
        cases = load_evaluation_cases(CASES_FILE)

        with self.assertRaisesRegex(ValueError, "未知 Agent 评测用例"):
            select_evaluation_cases(cases, ["missing-case"])

    def test_live_mode_requires_explicit_external_permission(self):
        with self.assertRaisesRegex(PermissionError, "--allow-external-model"):
            require_external_model_permission("live", allowed=False)

        # mock 不出网，无需额外开关；live 只有显式允许后才通过守卫。
        require_external_model_permission("mock", allowed=False)
        require_external_model_permission("live", allowed=True)

    def test_all_fixed_cases_run_against_the_isolated_mock_graph(self):
        # 这不是伪造响应：五条问题会真实经过 LangGraph 节点、工具和中断路由。
        cases = load_evaluation_cases(CASES_FILE)
        graph = build_isolated_evaluation_graph()
        runner = build_langgraph_runner(graph, mode="mock")

        summary = run_evaluation_cases(cases, runner)

        self.assertEqual(summary.total_cases, 5)
        self.assertEqual(summary.successful_cases, 5)
        self.assertEqual(summary.failed_cases, 0)
        self.assertEqual(summary.errored_cases, 0)
        self.assertEqual(summary.evaluation_completion_rate, 1.0)
        self.assertEqual(summary.scored_success_rate, 1.0)
        self.assertEqual(summary.failure_counts, {})

    @patch("agent_evaluation_runner.start_agent_graph")
    def test_langgraph_adapter_uses_isolated_thread_and_selected_mode(self, mocked_start):
        expected = build_response("live-case", tools=["query_ticket"], answer="查询完成")
        mocked_start.return_value = expected
        graph = object()
        runner = build_langgraph_runner(
            graph,
            mode="live",
            thread_id_factory=lambda case_id: f"fixed-{case_id}",
        )

        actual = runner("live-case", "查询工单 T-1003")

        self.assertIs(actual, expected)
        called_graph, request = mocked_start.call_args.args
        self.assertIs(called_graph, graph)
        self.assertEqual(request.thread_id, "fixed-live-case")
        self.assertEqual(request.message, "查询工单 T-1003")
        self.assertEqual(request.mode, "live")


if __name__ == "__main__":
    unittest.main()
