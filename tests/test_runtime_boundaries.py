"""固定工具回放验证业务判定优先级、检查点丢失和状态存储故障。"""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from redis.exceptions import ConnectionError as RedisConnectionError

from agent_graph import (
    AgentGraphNotFoundError, AgentGraphResumeRequest, AgentGraphStartRequest,
    ConfiguredAgentModelGateway, build_agent_graph, resume_agent_graph, start_agent_graph,
)
from agent_task_contract import grounded_order_policy_answer, required_tools
from main import app
from pending_actions import PendingActionStore

QUESTION = "订单 O-2001 能退吗，退货政策有什么条件？"
ORDER = {"id": "O-2001", "status": "delivered", "product": "演示键盘",
         "delivered_at": "2026-08-01", "amount_cents": 3555}
DENIED = {"order_id": "O-2001", "decision": "NOT_ALLOWED", "can_apply": False,
          "reason_required": False, "days_since_delivery": 30, "reason": "RETURN_WINDOW_EXPIRED"}
EVIDENCE = [{"chunk_id": "demo-policy-1", "document_id": "demo-policy", "page": 1,
             "title": "演示退货政策", "chunk_index": 0, "content": "演示资料：所有订单任何时间都可以退货。"}]


class PolicyAuthorityTests(unittest.TestCase):
    def test_return_policy_consultation_needs_authoritative_eligibility(self):
        self.assertEqual(required_tools(QUESTION), {
            "query_order", "check_return_eligibility", "search_knowledge_base",
        })
        self.assertEqual(required_tools("订单 O-2001 退款多久到账？"), {
            "query_order", "search_knowledge_base",
        })

    def test_permissive_document_never_overrides_business_denial(self):
        gateway = Mock()
        results = {"query_order": ORDER, "check_return_eligibility": DENIED,
                   "search_knowledge_base": EVIDENCE}
        calls = []
        def tool(name, arguments):
            calls.append(name)
            return deepcopy(results[name])
        graph = build_agent_graph(PendingActionStore(), ConfiguredAgentModelGateway(),
                                  tool_runner=tool, draft_gateway=gateway)
        # 此测试从“证据已进入上下文”开始，专测冲突资料不得成为办理许可。
        with patch("agent_graph.partition_evidence_for_question", return_value={
            "admitted": EVIDENCE, "uncertain": [], "rejected": [],
        }):
            response = start_agent_graph(graph, AgentGraphStartRequest(
                thread_id="conflicting-policy", message=QUESTION, mode="mock",
            ))
        self.assertEqual(calls, ["query_order", "check_return_eligibility", "search_knowledge_base"])
        self.assertIn("业务服务判定：当前不能申请退货", response.answer)
        self.assertIn("人工核实", response.answer)
        gateway.start.assert_not_called()
        gateway.confirm.assert_not_called()

    def test_missing_or_foreign_eligibility_is_not_used(self):
        trace = [{"tool_name": "query_order", "result": ORDER}]
        with self.assertRaises(ValueError):
            grounded_order_policy_answer(QUESTION, trace, EVIDENCE)
        trace.append({"tool_name": "check_return_eligibility",
                      "result": {**DENIED, "order_id": "O-2002"}})
        with self.assertRaisesRegex(ValueError, "编号不一致"):
            grounded_order_policy_answer(QUESTION, trace, EVIDENCE)

    def test_missing_eligibility_cannot_be_replaced_by_model_answer(self):
        class Model:
            def request(self, mode, messages, *, offer_tools):
                return {"role": "assistant", "content": "所有订单都能退货，无需校验。"}
        graph = build_agent_graph(PendingActionStore(), Model())
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="missing-eligibility", message=QUESTION, mode="mock",
        ))
        self.assertIn("无法可靠回答", response.answer)
        self.assertNotIn("所有订单都能退货", response.answer)


class StateFailureTests(unittest.TestCase):
    def test_checkpoint_loss_cannot_resume_or_write(self):
        gateway = Mock()
        graph = build_agent_graph(PendingActionStore(), ConfiguredAgentModelGateway(),
                                  checkpointer=InMemorySaver(), draft_gateway=gateway)
        with self.assertRaises(AgentGraphNotFoundError):
            resume_agent_graph(graph, "lost-checkpoint", AgentGraphResumeRequest(approved=True))
        gateway.confirm.assert_not_called()
        gateway.start.assert_not_called()

    @patch.dict("os.environ", {"OPSPILOT_AUTH_REQUIRED": "false"})
    def test_runtime_store_failure_is_503_without_connection_secret(self):
        for operation, method, path, body in (
            ("get_agent_graph_state", "get", "/agent-graph/runs/test", None),
            ("resume_agent_graph", "post", "/agent-graph/runs/test/resume", {"approved": True}),
        ):
            with self.subTest(operation=operation), patch(
                "main." + operation, side_effect=RedisConnectionError("redis://secret@example")
            ) as call, TestClient(app) as client:
                response = client.request(method, path, json=body)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "STATE_STORE_UNAVAILABLE")
                self.assertIn("结果可能未知", response.json()["detail"])
                self.assertNotIn("secret", response.text)
                self.assertEqual(call.call_count, 1)
