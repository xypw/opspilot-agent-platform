"""人工确认测试：申请不写入、确认执行一次、错误输入不创建操作。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from demo_confirmation import run_demo
from agent_loop import run_agent
from main import app
from pending_actions import (
    ActionNotFoundError,
    ActionStateError,
    PendingActionStore,
    TicketNotFoundError,
)
from tickets import TICKETS, query_ticket
from tool_args import RequestPriorityChangeArgs
from tool_executor import execute_tool
from tool_schema import REQUEST_PRIORITY_CHANGE_TOOL


class PendingActionTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.store = PendingActionStore(id_factory=lambda: "act_test_001")

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def test_proposal_does_not_change_ticket(self):
        before = query_ticket("T-1003").copy()
        action = self.store.propose_priority_change("T-1003", "high")
        self.assertEqual(action.status, "pending")
        self.assertEqual(action.previous_priority, "medium")
        self.assertEqual(action.new_priority, "high")
        self.assertEqual(query_ticket("T-1003"), before)

    def test_confirmation_executes_exactly_once(self):
        action = self.store.propose_priority_change("T-1003", "high")
        with patch.object(
            self.store._ticket_repository,
            "change_priority",
            wraps=self.store._ticket_repository.change_priority,
        ) as change:
            first = self.store.confirm(action.action_id)
            second = self.store.confirm(action.action_id)
        self.assertEqual(first.status, "executed")
        self.assertEqual(second, first)
        self.assertEqual(query_ticket("T-1003")["priority"], "high")
        change.assert_called_once_with("T-1003", "high")

    def test_unknown_ticket_does_not_create_action(self):
        with self.assertRaises(TicketNotFoundError):
            self.store.propose_priority_change("T-9999", "high")
        with self.assertRaises(ActionNotFoundError):
            self.store.get("act_test_001")

    def test_unknown_action_cannot_be_confirmed(self):
        with self.assertRaises(ActionNotFoundError):
            self.store.confirm("act_missing")

    def test_cancelled_action_cannot_be_confirmed_or_mutate_ticket(self):
        action = self.store.propose_priority_change("T-1003", "high")
        cancelled = self.store.cancel(action.action_id)

        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.store.cancel(action.action_id), cancelled)
        with self.assertRaises(ActionStateError):
            self.store.confirm(action.action_id)
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

    def test_argument_model_rejects_invalid_or_extra_values(self):
        for args in [
            {}, {"ticket_id": "T-1003"},
            {"ticket_id": "T-1003", "new_priority": "urgent"},
            {"ticket_id": "T-1003", "new_priority": "high", "confirm": True},
        ]:
            with self.subTest(args=args):
                with self.assertRaises(ValidationError):
                    RequestPriorityChangeArgs.model_validate(args)

    def test_schema_only_offers_a_request_not_direct_mutation(self):
        function = REQUEST_PRIORITY_CHANGE_TOOL["function"]
        self.assertEqual(function["name"], "request_priority_change")
        self.assertNotEqual(function["name"], "change_ticket_priority")
        self.assertEqual(
            function["parameters"]["required"], ["ticket_id", "new_priority"]
        )
        self.assertEqual(
            function["parameters"]["properties"]["new_priority"]["enum"],
            ["low", "medium", "high"],
        )

    def test_agent_tool_creates_pending_action_without_mutation(self):
        store = PendingActionStore(id_factory=lambda: "act_tool_001")
        before = query_ticket("T-1003").copy()
        with patch("tool_executor.ACTION_STORE", store):
            result = execute_tool(
                "request_priority_change",
                '{"ticket_id":"T-1003","new_priority":"high"}',
            )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["action_id"], "act_tool_001")
        self.assertEqual(query_ticket("T-1003"), before)

    def test_agent_model_request_creates_pending_action_without_mutation(self):
        store = PendingActionStore(id_factory=lambda: "act_agent_001")
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                message = {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_change_001", "type": "function",
                        "function": {
                            "name": "request_priority_change",
                            "arguments": '{"ticket_id":"T-1003","new_priority":"high"}',
                        },
                    }],
                }
            else:
                action = json.loads(payload["messages"][-1]["content"])
                self.assertEqual(action["status"], "pending")
                message = {
                    "role": "assistant",
                    "content": f"操作 {action['action_id']} 等待你的确认，尚未修改工单。",
                }
            return httpx.Response(200, json={"choices": [{"message": message}]})

        before = query_ticket("T-1003").copy()
        with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client, \
             patch("tool_executor.ACTION_STORE", store):
            answer = run_agent(
                "mock-only-not-a-real-key", client,
                "把工单 T-1003 的优先级改为 high。",
            )
        self.assertIn("act_agent_001", answer)
        self.assertIn("尚未修改", answer)
        self.assertEqual(query_ticket("T-1003"), before)
        self.assertEqual(len(requests), 2)
        self.assertIn("request_priority_change", {
            tool["function"]["name"] for tool in requests[0]["tools"]
        })

    def test_http_propose_confirm_and_repeat(self):
        store = PendingActionStore(id_factory=lambda: "act_api_001")
        with patch("main.ACTION_STORE", store):
            with TestClient(app) as client:
                proposed = client.post(
                    "/actions/priority-changes",
                    json={"ticket_id": "T-1003", "new_priority": "high"},
                )
                self.assertEqual(proposed.status_code, 200)
                self.assertEqual(proposed.json()["status"], "pending")
                self.assertEqual(client.get("/tickets/T-1003").json()["priority"], "medium")

                confirmed = client.post("/actions/act_api_001/confirm")
                repeated = client.post("/actions/act_api_001/confirm")
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json()["status"], "executed")
                self.assertEqual(repeated.json(), confirmed.json())
                self.assertEqual(client.get("/tickets/T-1003").json()["priority"], "high")

    def test_mock_chat_proposes_then_confirmation_executes(self):
        store = PendingActionStore(id_factory=lambda: "act_chat_001")
        with patch("tool_executor.ACTION_STORE", store), patch("main.ACTION_STORE", store):
            with TestClient(app) as client:
                proposed = client.post("/chat", json={
                    "message": "把工单 T-1003 的优先级改成 high",
                    "mode": "mock",
                })
                self.assertEqual(proposed.status_code, 200)
                data = proposed.json()
                self.assertEqual(data["tool_name"], "request_priority_change")
                self.assertEqual(data["tool_result"]["action_id"], "act_chat_001")
                self.assertEqual(data["tool_result"]["status"], "pending")
                self.assertIn("尚未修改", data["answer"])
                self.assertEqual(client.get("/tickets/T-1003").json()["priority"], "medium")

                confirmed = client.post("/actions/act_chat_001/confirm")
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json()["status"], "executed")
                self.assertEqual(client.get("/tickets/T-1003").json()["priority"], "high")

    def test_mock_change_requires_ticket_and_one_valid_priority(self):
        store = PendingActionStore(id_factory=lambda: "act_chat_001")
        for message in [
            "把工单 T-1003 的优先级改一下",
            "把订单 O-2003 的优先级改成 high",
            "把工单 T-1003 的优先级从 low 改成 high",
            "change T-1003 priority to urgent",
        ]:
            with self.subTest(message=message), \
                 patch("tool_executor.ACTION_STORE", store), patch("main.ACTION_STORE", store):
                with TestClient(app) as client:
                    response = client.post("/chat", json={"message": message, "mode": "mock"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(query_ticket("T-1003")["priority"], "medium")

    def test_priority_query_is_not_mistaken_for_a_change(self):
        with TestClient(app) as client:
            response = client.post("/chat", json={
                "message": "查询工单 T-1003 的优先级",
                "mode": "mock",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_name"], "query_ticket")

    def test_http_errors_are_explicit(self):
        store = PendingActionStore(id_factory=lambda: "act_api_001")
        with patch("main.ACTION_STORE", store):
            with TestClient(app) as client:
                missing_ticket = client.post(
                    "/actions/priority-changes",
                    json={"ticket_id": "T-9999", "new_priority": "high"},
                )
                missing_action = client.post("/actions/act_missing/confirm")
                invalid_priority = client.post(
                    "/actions/priority-changes",
                    json={"ticket_id": "T-1003", "new_priority": "urgent"},
                )
        self.assertEqual(missing_ticket.status_code, 404)
        self.assertEqual(missing_action.status_code, 404)
        self.assertEqual(invalid_priority.status_code, 422)

    def test_demo_restores_shared_ticket_data(self):
        before = deepcopy(TICKETS)
        trace = run_demo()
        self.assertEqual(trace["priority_before_confirmation"], "medium")
        self.assertEqual(trace["priority_after_confirmation"], "high")
        self.assertEqual(trace["repeated_confirmation_status"], "executed")
        self.assertEqual(TICKETS, before)


if __name__ == "__main__":
    unittest.main()
