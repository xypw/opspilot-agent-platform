"""Checkpoint 状态机与接口测试；全部使用 mock，不访问真实模型。"""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from checkpoint_store import (
    AgentRunStore,
    IdempotencyConflictError,
    RunNotFoundError,
    RunStateError,
)
from main import app
from pending_actions import PendingActionStore
from tickets import TICKETS, query_ticket


def pending_chat_result(action_id="act_checkpoint_001"):
    return {
        "tool_name": "request_priority_change",
        "tool_result": {
            "action_id": action_id,
            "tool_name": "change_ticket_priority",
            "ticket_id": "T-1003",
            "previous_priority": "medium",
            "new_priority": "high",
            "status": "pending",
        },
        "answer": "等待用户确认。",
        "model_requests": 0,
        "simulated_model_requests": 2,
    }


def query_chat_result():
    return {
        "tool_name": "query_ticket",
        "tool_result": {"id": "T-1003", "status": "open", "priority": "medium"},
        "answer": "工单优先级为 medium。",
        "model_requests": 0,
        "simulated_model_requests": 2,
    }


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.action_store = PendingActionStore(id_factory=lambda: "act_checkpoint_001")
        self.run_store = AgentRunStore(
            self.action_store, id_factory=lambda: "run_checkpoint_001"
        )

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def test_same_idempotency_key_returns_same_run_without_rerun(self):
        runner = Mock(return_value=query_chat_result())
        first = self.run_store.start("thread-1", "request-1", "查 T-1003", runner)
        second = self.run_store.start("thread-1", "request-1", "查 T-1003", runner)
        self.assertEqual(second, first)
        self.assertEqual(first.status, "COMPLETED")
        runner.assert_called_once_with("查 T-1003")

    def test_same_key_with_different_message_is_conflict(self):
        runner = Mock(return_value=query_chat_result())
        self.run_store.start("thread-1", "request-1", "查 T-1003", runner)
        with self.assertRaises(IdempotencyConflictError):
            self.run_store.start("thread-1", "request-1", "查 T-1002", runner)
        self.assertEqual(runner.call_count, 1)

    def test_pending_run_resumes_to_completed_and_executes_once(self):
        action = self.action_store.propose_priority_change("T-1003", "high")
        runner = Mock(return_value=pending_chat_result(action.action_id))
        run = self.run_store.start("thread-1", "change-1", "修改 T-1003", runner)
        self.assertEqual(run.status, "WAITING_CONFIRMATION")
        self.assertEqual(run.version, 1)
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

        with patch.object(self.action_store, "confirm", wraps=self.action_store.confirm) as confirm:
            completed = self.run_store.confirm(run.run_id)
            repeated = self.run_store.confirm(run.run_id)
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.version, 2)
        self.assertEqual(repeated, completed)
        self.assertIn("[模拟恢复]", completed.answer)
        self.assertEqual(query_ticket("T-1003")["priority"], "high")
        confirm.assert_called_once_with(action.action_id)

    def test_completed_query_run_cannot_be_confirmed(self):
        run = self.run_store.start(
            "thread-1", "query-1", "查 T-1003", lambda message: query_chat_result()
        )
        with self.assertRaises(RunStateError):
            self.run_store.confirm(run.run_id)

    def test_cancelled_run_never_executes_its_pending_action(self):
        action = self.action_store.propose_priority_change("T-1003", "high")
        run = self.run_store.start(
            "thread-1", "cancel-1", "修改 T-1003",
            Mock(return_value=pending_chat_result(action.action_id)),
        )

        cancelled = self.run_store.cancel(run.run_id)
        repeated = self.run_store.cancel(run.run_id)
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(cancelled.version, 2)
        self.assertEqual(repeated, cancelled)
        self.assertEqual(self.action_store.get(action.action_id).status, "cancelled")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")
        with self.assertRaises(RunStateError):
            self.run_store.confirm(run.run_id)

    def test_unknown_run_is_rejected(self):
        with self.assertRaises(RunNotFoundError):
            self.run_store.get("run_missing")
        with self.assertRaises(RunNotFoundError):
            self.run_store.confirm("run_missing")

    def test_failed_runner_leaves_no_checkpoint(self):
        def fail(message):
            raise ValueError("模拟失败")

        with self.assertRaises(ValueError):
            self.run_store.start("thread-1", "failed-1", "坏请求", fail)
        with self.assertRaises(RunNotFoundError):
            self.run_store.get("run_checkpoint_001")


class CheckpointApiTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.action_store = PendingActionStore(id_factory=lambda: "act_api_checkpoint_001")
        run_ids = iter(["run_api_001", "run_api_002", "run_api_003"])
        self.run_store = AgentRunStore(self.action_store, id_factory=lambda: next(run_ids))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def patches(self):
        return (
            patch("main.RUN_STORE", self.run_store),
            patch("tool_executor.ACTION_STORE", self.action_store),
        )

    def test_start_retry_get_confirm_and_repeat(self):
        first_patch, second_patch = self.patches()
        body = {
            "thread_id": "thread-demo-001",
            "idempotency_key": "change-t1003-v1",
            "message": "把工单 T-1003 的优先级改成 high",
        }
        with first_patch, second_patch:
            started = self.client.post("/agent/runs", json=body)
            retried = self.client.post("/agent/runs", json=body)
            fetched = self.client.get("/agent/runs/run_api_001")
            before = self.client.get("/tickets/T-1003")
            confirmed = self.client.post("/agent/runs/run_api_001/confirm")
            repeated = self.client.post("/agent/runs/run_api_001/confirm")
            after = self.client.get("/tickets/T-1003")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "WAITING_CONFIRMATION")
        self.assertEqual(started.json()["run_id"], "run_api_001")
        self.assertEqual(started.json()["pending_action_id"], "act_api_checkpoint_001")
        self.assertEqual(retried.json(), started.json())
        self.assertEqual(fetched.json(), started.json())
        self.assertEqual(before.json()["priority"], "medium")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "COMPLETED")
        self.assertEqual(confirmed.json()["version"], 2)
        self.assertEqual(repeated.json(), confirmed.json())
        self.assertEqual(after.json()["priority"], "high")

    def test_idempotency_conflict_returns_409_without_second_action(self):
        first_patch, second_patch = self.patches()
        with first_patch, second_patch:
            first = self.client.post("/agent/runs", json={
                "thread_id": "thread-1", "idempotency_key": "same-key",
                "message": "查工单 T-1003",
            })
            conflict = self.client.post("/agent/runs", json={
                "thread_id": "thread-1", "idempotency_key": "same-key",
                "message": "查工单 T-1002",
            })
        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)

    def test_query_run_completes_immediately_and_cannot_confirm(self):
        first_patch, second_patch = self.patches()
        with first_patch, second_patch:
            started = self.client.post("/agent/runs", json={
                "thread_id": "thread-1", "idempotency_key": "query-1",
                "message": "查询工单 T-1003 的优先级",
            })
            confirmed = self.client.post("/agent/runs/run_api_001/confirm")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "COMPLETED")
        self.assertIsNone(started.json()["pending_action_id"])
        self.assertEqual(confirmed.status_code, 409)

    def test_cancel_then_confirm_is_rejected_without_ticket_mutation(self):
        first_patch, second_patch = self.patches()
        body = {
            "thread_id": "thread-demo-001",
            "idempotency_key": "cancel-t1003-v1",
            "message": "把工单 T-1003 的优先级改成 high",
        }
        with first_patch, second_patch:
            started = self.client.post("/agent/runs", json=body)
            cancelled = self.client.post("/agent/runs/run_api_001/cancel")
            repeated_cancel = self.client.post("/agent/runs/run_api_001/cancel")
            confirmed = self.client.post("/agent/runs/run_api_001/confirm")
            ticket = self.client.get("/tickets/T-1003")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(cancelled.json()["version"], 2)
        self.assertEqual(repeated_cancel.json(), cancelled.json())
        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(ticket.json()["priority"], "medium")

    def test_unknown_run_and_invalid_body_are_rejected(self):
        first_patch, second_patch = self.patches()
        with first_patch, second_patch:
            missing_get = self.client.get("/agent/runs/run_missing")
            missing_confirm = self.client.post("/agent/runs/run_missing/confirm")
            invalid = self.client.post("/agent/runs", json={
                "thread_id": " ", "idempotency_key": "key", "message": "查 T-1003",
            })
        self.assertEqual(missing_get.status_code, 404)
        self.assertEqual(missing_confirm.status_code, 404)
        self.assertEqual(invalid.status_code, 422)

    def test_start_is_offline_and_does_not_read_key(self):
        first_patch, second_patch = self.patches()
        with first_patch, second_patch, \
             patch("chat_service.load_api_key") as load_key, \
             patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("不允许出网")) as network:
            response = self.client.post("/agent/runs", json={
                "thread_id": "thread-1", "idempotency_key": "query-1",
                "message": "查工单 T-1003",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model_requests"], 0)
        load_key.assert_not_called()
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
