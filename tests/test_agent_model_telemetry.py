"""模型可观测性在 LangGraph State 与公开响应中的传播测试。"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
