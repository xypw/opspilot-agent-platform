"""LangGraph Agent 测试：工具路由、RAG 引用与人工确认。"""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from agent_graph import (
    AgentGraphResumeRequest,
    AgentGraphStartRequest,
    ConfiguredAgentModelGateway,
    build_agent_graph,
    get_agent_graph_state,
    resume_agent_graph,
    start_agent_graph,
)
from main import app
from knowledge_base import KnowledgeStoreUnavailableError
from pending_actions import PendingActionStore
from preview_tool_call import ModelAPIError
from tickets import TICKETS, query_ticket


class DirectAnswerGateway:
    """测试用模型网关：记录收到的消息后直接返回最终文字。"""

    def __init__(self):
        # 保存节点发给模型的消息，供测试检查系统提示词是否存在。
        self.received_messages: list[dict] | None = None

    def request(self, mode, messages, *, offer_tools):
        # 复制列表，避免后续节点追加工具消息影响本次断言。
        self.received_messages = messages.copy()
        # 这条测试只验证首次模型调用，所以必须仍然允许模型选择工具。
        if not offer_tools:
            # 若图错误地再次调用模型，这里故意失败以暴露流程问题。
            raise AssertionError("直接回答不应有第二次模型调用。")
        # 返回无工具的文字，验证 call_model 可以直接结束。
        return {"role": "assistant", "content": "请提供要查询的编号。"}


class InvalidToolGateway:
    """测试用模型网关：模拟上游模型给出缺少必填参数的工具调用。"""

    def request(self, mode, messages, *, offer_tools):
        # 返回形式正确但参数错误的工具调用，应该在 Pydantic 层被拒绝。
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "invalid_tool_call",
                "type": "function",
                "function": {"name": "query_ticket", "arguments": "{}"},
            }],
        }


class EndlessReadGateway:
    """持续请求同一个只读工具，用来验证图的工具步数上限。"""

    def __init__(self):
        self.offer_tools_history: list[bool] = []

    def request(self, mode, messages, *, offer_tools):
        self.offer_tools_history.append(offer_tools)
        if not offer_tools:
            return {"role": "assistant", "content": "已根据限定步骤结束查询。"}
        call_number = len(self.offer_tools_history)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"loop_call_{call_number}",
                "type": "function",
                "function": {
                    "name": "query_ticket",
                    "arguments": '{"ticket_id":"T-1003"}',
                },
            }],
        }


class PrematureCompoundGateway:
    """模型提前宣称任务完成，用于验证联合问题的完成门槛。"""

    def __init__(self):
        self.calls = 0

    def request(self, mode, messages, *, offer_tools):
        self.calls += 1
        return {"role": "assistant", "content": "订单明天到账。"}


class HallucinatedCompoundGateway:
    """模型查完两类资料后仍编造具体到账日。"""

    def __init__(self):
        self.calls = 0

    def request(self, mode, messages, *, offer_tools):
        self.calls += 1
        if self.calls <= 2:
            name, arguments = (
                ("query_order", '{"order_id":"O-2001"}') if self.calls == 1
                else ("search_knowledge_base", '{"query":"退款多久到账","limit":3}')
            )
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"compound-{self.calls}", "type": "function",
                "function": {"name": name, "arguments": arguments},
            }]}
        return {"role": "assistant", "content": "订单 O-2001 的退款明天到账。"}


class AgentGraphTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.store = PendingActionStore(id_factory=lambda: "act_agent_graph_001")
        self.draft_gateway = Mock()
        self.draft_gateway.start.return_value = {
            "draft_id": "00000000-0000-0000-0000-000000000001",
            "order_id": "O-2001", "product": "机械键盘", "amount_cents": 39900,
            "started_at": "2026-09-07T15:30:00Z", "expires_at": "2026-09-07T16:30:00Z",
            "status": "WAITING_REASON",
        }
        self.graph = build_agent_graph(
            self.store, ConfiguredAgentModelGateway(), draft_gateway=self.draft_gateway
        )

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def start(self, message: str, thread_id: str = "agent-thread-001"):
        return start_agent_graph(self.graph, AgentGraphStartRequest(
            thread_id=thread_id,
            message=message,
            mode="mock",
        ))

    def test_ticket_query_routes_model_to_read_tool_and_final_answer(self):
        response = self.start("帮我查工单 T-1003")

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_name, "query_ticket")
        self.assertEqual(response.tool_result["id"], "T-1003")
        self.assertIn("medium", response.answer)
        self.assertEqual(response.simulated_model_requests, 2)

    def test_start_includes_system_prompt_before_user_message(self):
        # 创建只会直接回答的网关，隔离测试消息构造而不执行真实模型。
        gateway = DirectAnswerGateway()
        # 使用独立图，避免本测试依赖 setUp 中的 mock 网关实现。
        graph = build_agent_graph(self.store, gateway)
        # 启动一次 Agent，让 call_model 节点接收初始 messages。
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="prompt-thread-001",
            message="查工单",
            mode="mock",
        ))
        # 无工具文字回答应正常结束。
        self.assertEqual(response.status, "COMPLETED")
        # 第一个消息必须是系统提示词，而不是把权限规则丢给用户消息。
        self.assertEqual(gateway.received_messages[0]["role"], "system")
        # 第二个消息才是用户本次真实输入。
        self.assertEqual(gateway.received_messages[1], {"role": "user", "content": "查工单"})

    def test_order_query_uses_order_tool(self):
        response = self.start("查询订单 O-2001")

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_name, "query_order")
        self.assertEqual(response.tool_result["product"], "机械键盘")

    def test_return_question_routes_to_java_eligibility_tool(self):
        eligibility = {
            "order_id": "O-2001",
            "decision": "NO_REASON_ALLOWED",
            "can_apply": True,
            "reason_required": False,
            "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        with patch("tool_executor.RETURN_ELIGIBILITY_QUERY", return_value=eligibility) as query:
            response = self.start("订单 O-2001 可以退货吗？")

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_name, "check_return_eligibility")
        self.assertIn("可以申请退货", response.answer)
        self.assertIsNone(response.return_draft)
        self.draft_gateway.start.assert_not_called()
        query.assert_called_once_with("O-2001")

    def test_explicit_return_application_creates_draft_and_waits_for_confirmation(self):
        eligibility = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        with patch("tool_executor.RETURN_ELIGIBILITY_QUERY", return_value=eligibility):
            response = self.start("请帮我申请退货，订单 O-2001", thread_id="apply-return-001")
        self.assertEqual(response.status, "WAITING_CONFIRMATION")
        self.assertEqual(response.return_draft.product, "机械键盘")
        self.draft_gateway.start.assert_called_once_with("O-2001")

    def test_knowledge_question_routes_to_rag_and_adds_programmatic_citation(self):
        response = self.start("退款多久到账")

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_name, "search_knowledge_base")
        self.assertIn("三个工作日", response.answer)
        self.assertIn("来源：《售后与退款制度》第2页", response.answer)

    def test_refund_how_to_abstains_without_procedure_evidence(self):
        response = self.start("我应该怎么退款")

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_name, "search_knowledge_base")
        self.assertEqual(response.tool_result, [])
        self.assertIn("没有找到足够证据", response.answer)
        self.assertIn("人工客服", response.answer)
        self.assertNotIn("来源：", response.answer)
        self.assertEqual(response.simulated_model_requests, 1)

    def test_knowledge_store_failure_is_reported_as_503_not_empty_evidence(self):
        def unavailable_tool(tool_name, arguments_json):
            raise KnowledgeStoreUnavailableError("数据库不可用")

        graph = build_agent_graph(
            self.store, ConfiguredAgentModelGateway(), tool_runner=unavailable_tool
        )
        with patch("main.AGENT_GRAPH", graph):
            response = TestClient(app).post("/agent-graph/runs", json={
                "thread_id": "database-failure-thread",
                "message": "退款多久到账",
                "mode": "mock",
            })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "知识库暂时无法检索文档"})

    def test_compound_question_runs_order_then_rag_and_returns_trace(self):
        response = self.start(
            "查询订单 O-2001，并告诉我退款多久到账",
            thread_id="multi-tool-thread-001",
        )

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_steps, 2)
        self.assertEqual(
            [step.tool_name for step in response.tool_trace],
            ["query_order", "search_knowledge_base"],
        )
        self.assertIn("机械键盘", response.answer)
        self.assertIn("三个工作日", response.answer)
        self.assertIn("来源：《售后与退款制度》第2页", response.answer)
        self.assertIn("无法给出这笔订单的具体到账日期", response.answer)
        self.assertEqual(response.simulated_model_requests, 3)

    def test_compound_question_retries_once_then_abstains_when_model_skips_tools(self):
        gateway = PrematureCompoundGateway()
        graph = build_agent_graph(self.store, gateway)
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="premature-compound-001",
            message="订单 O-2001 的退款多久到账？",
            mode="mock",
        ))
        self.assertEqual(gateway.calls, 2)
        self.assertEqual(response.tool_steps, 0)
        self.assertIn("尚未同时取得订单信息和政策证据", response.answer)
        self.assertNotIn("明天到账", response.answer)

    def test_compound_answer_uses_order_and_policy_not_model_claim(self):
        graph = build_agent_graph(self.store, HallucinatedCompoundGateway())
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="grounded-compound-001",
            message="订单 O-2001 的退款多久到账？",
            mode="mock",
        ))
        self.assertEqual(response.tool_steps, 2)
        self.assertIn("机械键盘", response.answer)
        self.assertIn("三个工作日", response.answer)
        self.assertNotIn("明天到账", response.answer)
        self.assertIn("来源：《售后与退款制度》第2页", response.answer)

    def test_order_and_return_shipping_policy_does_not_create_return_draft(self):
        response = self.start(
            "订单 O-2001 的退货运费政策是什么？",
            thread_id="shipping-policy-001",
        )
        self.assertEqual(response.tool_steps, 2)
        self.assertEqual(
            [step.tool_name for step in response.tool_trace],
            ["query_order", "search_knowledge_base"],
        )
        self.draft_gateway.start.assert_not_called()

    def test_read_tool_loop_stops_offering_tools_after_three_steps(self):
        gateway = EndlessReadGateway()
        graph = build_agent_graph(self.store, gateway)

        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="bounded-loop-thread-001",
            message="重复查询也必须停止",
            mode="mock",
        ))

        self.assertEqual(response.status, "COMPLETED")
        self.assertEqual(response.tool_steps, 3)
        self.assertEqual(len(response.tool_trace), 3)
        self.assertEqual(gateway.offer_tools_history, [True, True, True, False])
        self.assertEqual(response.simulated_model_requests, 4)

    def test_priority_change_pauses_then_confirmation_executes(self):
        waiting = self.start("把工单 T-1003 的优先级改成 high")
        self.assertEqual(waiting.status, "WAITING_CONFIRMATION")
        self.assertEqual(waiting.tool_name, "request_priority_change")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

        completed = resume_agent_graph(
            self.graph,
            "agent-thread-001",
            AgentGraphResumeRequest(approved=True),
        )
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.tool_result["status"], "executed")
        self.assertEqual(query_ticket("T-1003")["priority"], "high")

    def test_priority_change_rejection_cancels_without_mutation(self):
        self.start("把工单 T-1003 的优先级改成 high")

        cancelled = resume_agent_graph(
            self.graph,
            "agent-thread-001",
            AgentGraphResumeRequest(approved=False),
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(cancelled.tool_result["status"], "cancelled")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

    def test_get_state_reads_a_completed_query_without_running_model_again(self):
        started = self.start("查询订单 O-2001", thread_id="state-thread-001")

        restored = get_agent_graph_state(self.graph, "state-thread-001")

        self.assertEqual(restored, started)
        self.assertEqual(restored.status, "COMPLETED")
        self.assertEqual(restored.tool_name, "query_order")

    def test_get_state_rejects_unknown_thread(self):
        with self.assertRaisesRegex(LookupError, "从未启动"):
            get_agent_graph_state(self.graph, "state-thread-missing")

    def test_fastapi_exposes_agent_graph_start_and_resume(self):
        with patch("main.AGENT_GRAPH", self.graph):
            with TestClient(app) as client:
                waiting = client.post("/agent-graph/runs", json={
                    "thread_id": "agent-api-001",
                    "message": "把工单 T-1003 的优先级改成 high",
                    "mode": "mock",
                })
                completed = client.post(
                    "/agent-graph/runs/agent-api-001/resume",
                    json={"approved": True},
                )

        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json()["status"], "WAITING_CONFIRMATION")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "COMPLETED")

    def test_fastapi_hides_invalid_model_tool_parameters(self):
        # 构造故意给出非法工具参数的图，模拟不可靠的上游模型输出。
        invalid_graph = build_agent_graph(self.store, InvalidToolGateway())
        # 替换 FastAPI 使用的图，确保测试不访问网络。
        with patch("main.AGENT_GRAPH", invalid_graph):
            # 用 TestClient 像前端一样请求接口。
            with TestClient(app) as client:
                # 发起一个模式合法的 mock 请求。
                response = client.post("/agent-graph/runs", json={
                    "thread_id": "invalid-tool-thread",
                    "message": "查工单 T-1003",
                    "mode": "mock",
                })
        # 模型输出不符合工具参数契约，网关错误对前端表现为上游错误。
        self.assertEqual(response.status_code, 502)
        # 不回显原始模型工具参数，避免泄露内部模型输出。
        self.assertEqual(response.json(), {"detail": "模型返回的工具调用或参数不符合约定。"})

    def test_fastapi_maps_evidence_reviewer_model_errors(self):
        cases = (
            (ModelAPIError(429), 503, "真实模型凭据、权限或配额不可用。"),
            (ModelAPIError(500), 502, "上游模型请求失败，本次流程未完成。"),
        )
        for error, status_code, detail in cases:
            with self.subTest(status_code=status_code):
                with patch("main.start_agent_graph", side_effect=error):
                    response = TestClient(app).post("/agent-graph/runs", json={
                        "thread_id": f"review-error-{status_code}",
                        "message": "退款多久到账",
                        "mode": "live",
                    })
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json(), {"detail": detail})

    def test_fastapi_reads_agent_state_by_thread_id(self):
        with patch("main.AGENT_GRAPH", self.graph):
            with TestClient(app) as client:
                started = client.post("/agent-graph/runs", json={
                    "thread_id": "state-api-001",
                    "message": "查询订单 O-2001",
                    "mode": "mock",
                })
                restored = client.get("/agent-graph/runs/state-api-001")
                missing = client.get("/agent-graph/runs/state-api-missing")

        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json(), started.json())
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
