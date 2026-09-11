"""模型可观测性在 LangGraph State 与公开响应中的传播测试。"""

import unittest

from agent_evaluation import AgentEvaluationCase
from agent_evaluation_runner import build_langgraph_runner, run_evaluation_cases
from agent_graph import (
    AgentGraphStartRequest,
    AgentModelReply,
    build_agent_graph,
    start_agent_graph,
)
from pending_actions import PendingActionStore
from retry_policy import ModelRequestTelemetry


class TelemetryGateway:
    """返回一次带有三次 HTTP 尝试的成功模型轮次。"""

    def request(self, mode, messages, *, offer_tools):
        return AgentModelReply(
            message={"role": "assistant", "content": "已完成。"},
            telemetry=ModelRequestTelemetry(
                http_attempts=3,
                retry_count=2,
                duration_ms=620.0,
                attempt_durations_ms=[10.0, 20.0, 180.0],
            ),
        )


class AgentModelTelemetryTests(unittest.TestCase):
    def test_live_model_telemetry_reaches_public_response(self):
        graph = build_agent_graph(PendingActionStore(), TelemetryGateway())

        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="telemetry-thread",
            message="测试模型指标",
            mode="live",
        ))

        self.assertEqual(response.model_requests, 1)
        self.assertEqual(response.model_http_attempts, 3)
        self.assertEqual(response.model_retry_count, 2)
        self.assertEqual(response.model_turn_durations_ms, [620.0])
        self.assertEqual(
            response.model_http_attempt_durations_ms,
            [10.0, 20.0, 180.0],
        )

    def test_real_checkpoint_merges_successful_turn_before_later_error(self):
        class SecondTurnError(Exception):
            model_http_attempts = 3
            model_retry_count = 2
            model_turn_durations_ms = [46000.0]
            model_http_attempt_durations_ms = [15000.0, 15000.0, 15400.0]

        class OneSuccessThenErrorGateway:
            def __init__(self):
                self.calls = 0

            def request(self, mode, messages, *, offer_tools):
                self.calls += 1
                if self.calls == 1:
                    return AgentModelReply(
                        message={
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "telemetry-tool-call",
                                "type": "function",
                                "function": {
                                    "name": "query_ticket",
                                    "arguments": '{"ticket_id":"T-1003"}',
                                },
                            }],
                        },
                        telemetry=ModelRequestTelemetry(
                            http_attempts=1,
                            retry_count=0,
                            duration_ms=18000.0,
                            attempt_durations_ms=[18000.0],
                        ),
                    )
                raise SecondTurnError("第二轮模型失败")

        graph = build_agent_graph(PendingActionStore(), OneSuccessThenErrorGateway())
        runner = build_langgraph_runner(graph, mode="live")
        case = AgentEvaluationCase(
            case_id="real-checkpoint-merge",
            question="查询工单 T-1003",
            expected_tools=["query_ticket"],
        )

        result = run_evaluation_cases([case], runner).results[0]

        self.assertEqual(result.error_type, "SecondTurnError")
        self.assertEqual(result.model_requests, 1)
        self.assertEqual(result.model_http_attempts, 4)
        self.assertEqual(result.model_retry_count, 2)
        self.assertEqual(result.model_turn_durations_ms, [18000.0, 46000.0])


if __name__ == "__main__":
    unittest.main()
