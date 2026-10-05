"""审查发现的权限、最终答案和证据裁剪缺陷；全部使用离线依赖。"""

import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.types import Command

from agent_graph import (
    AgentGraphStartRequest, ConfiguredAgentModelGateway, build_agent_graph,
    get_agent_graph_state, start_agent_graph,
)
from evidence_review import verify_evidence_review
from pending_actions import PendingActionStore
from preview_tool_call import build_initial_messages, request_message
from service_api import build_service_router
from tool_executor import ConfiguredToolExecutor


TIMING = {"chunk_id": "refund-time", "title": "退款政策", "page": 1,
          "content": "退款审核通过后三个工作日到账。", "score": 0.95}
DESTINATION = {"chunk_id": "refund-destination", "title": "退款政策", "page": 2,
               "content": "退款款项原路退回原支付账户。", "score": 0.94}
COMPOUND_QUESTION = "退款多久到账，退回哪个账户？"
TOKEN = "offline-review-customer-token"
AUTH_ENV = {
    "OPSPILOT_AUTH_REQUIRED": "true",
    "OPSPILOT_AUTH_TOKENS": json.dumps({TOKEN: {"user_id": "U-1002", "roles": ["customer"]}}),
    "OPSPILOT_INTERNAL_SERVICE_TOKEN": "offline-review-internal-token",
}


def support_all(question, candidates):
    return {"supported": True, "supporting_quotes": [
        {"chunk_id": item["chunk_id"], "text": item["content"]} for item in candidates
    ], "missing_information": ""}


class ScriptedKnowledgeGateway:
    def __init__(self, question="退款多久到账", limit=3):
        self.question, self.limit, self.calls = question, limit, 0

    def request(self, mode, messages, *, offer_tools):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "read-evidence", "type": "function", "function": {
                    "name": "search_knowledge_base", "arguments": json.dumps({
                        "query": self.question, "limit": self.limit,
                    }),
                },
            }]}
        return {"role": "assistant", "content": "退款审核通过后24小时到账。"}


class DeploymentTicketBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock()
        self.repository.get_by_id.return_value = {
            "id": "T-1003", "status": "open", "priority": "medium",
        }
        self.repository.change_priority.return_value = {
            "id": "T-1003", "status": "open", "priority": "high",
        }
        self.store = PendingActionStore(ticket_repository=self.repository)
        self.executor = ConfiguredToolExecutor(
            ticket_repository=self.repository, action_store=self.store,
            order_query=Mock(), return_eligibility_query=Mock(),
        )
        self.graph = build_agent_graph(
            self.store, ConfiguredAgentModelGateway(), tool_runner=self.executor.execute,
        )

    @patch.dict(os.environ, AUTH_ENV)
    def test_deployment_prompt_explains_ticket_capabilities_are_unavailable(self):
        messages = build_initial_messages("查询工单 T-1003，并把优先级改为 high")
        system = messages[0]["content"]
        self.assertNotIn("query_ticket", system)
        self.assertNotIn("request_priority_change", system)
        self.assertNotIn("action_id", system)
        self.assertIn("旧工单查询与优先级修改能力不可用", system)
        self.assertIn("不要索取工单编号", system)
        for enabled_tool in ("query_order", "check_return_eligibility", "search_knowledge_base"):
            self.assertIn(enabled_tool, system)
        self.assertEqual(messages[1]["content"], "查询工单 T-1003，并把优先级改为 high")

    @patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"})
    def test_offline_prompt_retains_legacy_ticket_instructions(self):
        system = build_initial_messages()[0]["content"]
        self.assertIn("工单查询使用 query_ticket", system)
        self.assertIn("用户要求修改工单优先级时使用 request_priority_change", system)
        self.assertIn("必须提醒用户确认 action_id，不能声称已经修改", system)
        self.assertIn("具体订单、工单或退货资格而用户未提供对应编号时，才先询问编号", system)
        self.assertNotIn("旧工单查询与优先级修改能力不可用", system)

    @patch.dict(os.environ, AUTH_ENV)
    def test_model_request_does_not_offer_legacy_ticket_tools(self):
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "请提供订单号。",
            }}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch(
            "preview_tool_call.acquire_model_request"
        ):
            request_message("offline-key", client, [{"role": "user", "content": "查订单"}])
        names = {item["function"]["name"] for item in payloads[0]["tools"]}
        self.assertNotIn("query_ticket", names)
        self.assertNotIn("request_priority_change", names)
        self.assertIn("query_order", names)

    @patch.dict(os.environ, AUTH_ENV)
    def test_direct_tool_execution_denies_ticket_reads_and_changes(self):
        for name, arguments in (
            ("query_ticket", {"ticket_id": "T-1003"}),
            ("request_priority_change", {"ticket_id": "T-1003", "new_priority": "high"}),
        ):
            with self.subTest(tool=name), self.assertRaises(PermissionError):
                self.executor.execute(name, json.dumps(arguments))
        self.repository.get_by_id.assert_not_called()
        self.repository.change_priority.assert_not_called()

    @patch.dict(os.environ, AUTH_ENV)
    def test_customer_cannot_enter_legacy_ticket_flow_through_agent_api(self):
        from main import app
        with TestClient(app) as client, patch("main.AGENT_GRAPH", self.graph):
            for message in ("查询工单 T-1003", "把工单 T-1003 优先级改为 high"):
                with self.subTest(message=message):
                    response = client.post("/agent-graph/runs", headers={
                        "Authorization": f"Bearer {TOKEN}",
                    }, json={"thread_id": message, "message": message, "mode": "mock"})
                    self.assertEqual(response.status_code, 403)
        self.repository.get_by_id.assert_not_called()
        self.repository.change_priority.assert_not_called()

    def _create_legacy_checkpoint(self):
        with patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"}):
            response = start_agent_graph(self.graph, AgentGraphStartRequest(
                thread_id="legacy-ticket", message="把工单 T-1003 优先级改为 high", mode="mock",
            ), user_id="U-1002")
        self.assertEqual(response.status, "WAITING_CONFIRMATION")

    def test_old_ticket_checkpoint_cannot_be_read_or_resumed_after_auth_enabled(self):
        from main import app
        self._create_legacy_checkpoint()
        with patch.dict(os.environ, AUTH_ENV), patch("main.AGENT_GRAPH", self.graph), TestClient(app) as client:
            headers = {"Authorization": f"Bearer {TOKEN}"}
            self.assertEqual(client.get("/agent-graph/runs/legacy-ticket", headers=headers).status_code, 403)
            response = client.post("/agent-graph/runs/legacy-ticket/resume", headers=headers,
                                   json={"approved": True})
            self.assertEqual(response.status_code, 403)
        self.repository.change_priority.assert_not_called()

    def test_graph_node_rejects_old_checkpoint_even_without_http_resume_wrapper(self):
        self._create_legacy_checkpoint()
        with patch.dict(os.environ, AUTH_ENV), self.assertRaises(PermissionError):
            self.graph.invoke(Command(resume={"approved": True}),
                              config={"configurable": {"thread_id": "U-1002:legacy-ticket"}})
        self.repository.change_priority.assert_not_called()

    def test_pending_write_checkpoint_is_rejected_before_retrying_execution(self):
        self._create_legacy_checkpoint()
        config = {"configurable": {"thread_id": "U-1002:legacy-ticket"}}
        # 模拟确认已保存、真正写节点尚未执行时服务切换到了鉴权部署模式。
        self.graph.update_state(config, {"approved": True}, as_node="await_approval")
        with patch.dict(os.environ, AUTH_ENV), self.assertRaises(PermissionError):
            self.graph.invoke(None, config=config)
        self.repository.change_priority.assert_not_called()

    def test_completed_ticket_checkpoint_is_hidden_after_auth_enabled(self):
        with patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"}):
            response = start_agent_graph(self.graph, AgentGraphStartRequest(
                thread_id="legacy-query", message="查询工单 T-1003", mode="mock",
            ), user_id="U-1002")
        self.assertEqual(response.tool_result["id"], "T-1003")
        with patch.dict(os.environ, AUTH_ENV), self.assertRaises(PermissionError):
            get_agent_graph_state(self.graph, "legacy-query", user_id="U-1002")

    @patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"})
    def test_offline_ticket_teaching_flow_still_executes_after_confirmation(self):
        self._create_legacy_checkpoint()
        state = self.graph.invoke(Command(resume={"approved": True}),
                                  config={"configurable": {"thread_id": "U-1002:legacy-ticket"}})
        self.assertEqual(state["status"], "COMPLETED")
        self.repository.change_priority.assert_called_once_with("T-1003", "high")


@patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"})
class GroundedAnswerBoundaryTests(unittest.TestCase):
    def test_final_model_cannot_replace_verified_three_days_with_24_hours(self):
        graph = build_agent_graph(
            PendingActionStore(), ScriptedKnowledgeGateway(),
            tool_runner=lambda *_: [TIMING], evidence_reviewer=support_all,
            evidence_review_modes=frozenset({"mock"}),
        )
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="grounded-answer", message="退款多久到账", mode="mock",
        ))
        self.assertIn(TIMING["content"], response.answer)
        self.assertNotIn("24小时", response.answer)
        self.assertIn("来源：《退款政策》第1页", response.answer)

    def test_agent_does_not_reuse_sufficiency_verdict_after_dropping_required_quote(self):
        graph = build_agent_graph(
            PendingActionStore(), ScriptedKnowledgeGateway(COMPOUND_QUESTION, limit=1),
            tool_runner=lambda *_: [TIMING, DESTINATION], evidence_reviewer=support_all,
            evidence_review_modes=frozenset({"mock"}),
        )
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="bounded-agent", message=COMPOUND_QUESTION, mode="mock",
        ))
        self.assertEqual(response.tool_result, [])
        self.assertNotIn("来源：", response.answer)
        state = graph.get_state({"configurable": {"thread_id": "bounded-agent"}}).values
        self.assertFalse(state["evidence_review"]["supported"])
        self.assertTrue(state["evidence_review"]["missing_information"])

    def test_agent_returns_both_reviewed_facts_when_limit_fits(self):
        graph = build_agent_graph(
            PendingActionStore(), ScriptedKnowledgeGateway(COMPOUND_QUESTION, limit=2),
            tool_runner=lambda *_: [TIMING, DESTINATION], evidence_reviewer=support_all,
            evidence_review_modes=frozenset({"mock"}),
        )
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="complete-answer", message=COMPOUND_QUESTION, mode="mock",
        ))
        self.assertIn(TIMING["content"], response.answer)
        self.assertIn(DESTINATION["content"], response.answer)
        self.assertNotIn("24小时", response.answer)
        self.assertEqual(len(response.tool_result), 2)

    def test_limit_counts_distinct_chunks_and_keeps_all_quotes_within_one_chunk(self):
        combined = {**TIMING, "content": TIMING["content"] + DESTINATION["content"]}
        raw = {"supported": True, "supporting_quotes": [
            {"chunk_id": combined["chunk_id"], "text": fact["content"]}
            for fact in (TIMING, DESTINATION)
        ], "missing_information": ""}
        verdict, evidence = verify_evidence_review(raw, [combined], limit=1)
        self.assertTrue(verdict.supported)
        self.assertEqual(len(evidence), 1)
        self.assertIn(TIMING["content"], evidence[0]["content"])
        self.assertIn(DESTINATION["content"], evidence[0]["content"])

    def test_service_preserves_complete_reviewed_evidence_or_reports_insufficient(self):
        app = FastAPI()
        app.include_router(build_service_router(
            order_query=Mock(), eligibility_query=Mock(), draft_gateway=Mock(),
            evidence_reviewer=support_all,
        ))
        with TestClient(app) as client, patch(
            "service_api.search_knowledge_candidates", return_value=[TIMING, DESTINATION],
        ):
            insufficient = client.post("/service/knowledge/answer", json={
                "query": COMPOUND_QUESTION, "limit": 1, "review_mode": "live",
            })
            sufficient = client.post("/service/knowledge/answer", json={
                "query": COMPOUND_QUESTION, "limit": 2, "review_mode": "live",
            })
        self.assertEqual(insufficient.status_code, 200)
        self.assertEqual(insufficient.json()["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(insufficient.json()["evidence"], [])
        self.assertTrue(insufficient.json()["missing_information"])
        self.assertEqual(sufficient.json()["status"], "SUPPORTED")
        self.assertEqual(len(sufficient.json()["evidence"]), 2)
        self.assertIn(DESTINATION["content"], sufficient.json()["answer"])


if __name__ == "__main__":
    unittest.main()
