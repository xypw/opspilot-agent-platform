"""LangGraph 人工确认工作流测试：暂停、同意、拒绝和 FastAPI 契约。"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from main import app
from pending_actions import PendingActionStore
from priority_change_graph import (
    PriorityChangeResumeRequest,
    PriorityChangeStartRequest,
    WorkflowNotFoundError,
    WorkflowStateError,
    build_priority_change_graph,
    resume_priority_change_workflow,
    start_priority_change_workflow,
)
from tickets import TICKETS, query_ticket


class PriorityChangeGraphTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.store = PendingActionStore(id_factory=lambda: "act_graph_001")
        self.graph = build_priority_change_graph(self.store, InMemorySaver())

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def start(self, thread_id: str = "thread-001"):
        return start_priority_change_workflow(
            self.graph,
            PriorityChangeStartRequest(
                thread_id=thread_id,
                ticket_id="T-1003",
                new_priority="high",
            ),
        )

    def test_start_pauses_without_mutating_ticket(self):
        response = self.start()

        self.assertEqual(response.status, "WAITING_CONFIRMATION")
        self.assertEqual(response.action_id, "act_graph_001")
        self.assertEqual(response.tool_result["status"], "pending")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

    def test_approval_resumes_and_executes_once(self):
        self.start()

        completed = resume_priority_change_workflow(
            self.graph,
            "thread-001",
            PriorityChangeResumeRequest(approved=True),
        )

        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.tool_result["status"], "executed")
        self.assertEqual(query_ticket("T-1003")["priority"], "high")

    def test_rejection_resumes_and_cancels_without_mutation(self):
        self.start()

        cancelled = resume_priority_change_workflow(
            self.graph,
            "thread-001",
            PriorityChangeResumeRequest(approved=False),
        )

        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(cancelled.tool_result["status"], "cancelled")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

    def test_invalid_resume_payload_is_rejected(self):
        self.start()
        config = {"configurable": {"thread_id": "thread-001"}}

        with self.assertRaisesRegex(ValueError, "approved"):
            self.graph.invoke(Command(resume={"approved": "yes"}), config=config)

    def test_unknown_thread_id_does_not_start_a_new_incomplete_workflow(self):
        with self.assertRaises(WorkflowNotFoundError):
            resume_priority_change_workflow(
                self.graph,
                "thread-does-not-exist",
                PriorityChangeResumeRequest(approved=True),
            )

    def test_completed_workflow_cannot_be_resumed_again(self):
        self.start()
        resume_priority_change_workflow(
            self.graph,
            "thread-001",
            PriorityChangeResumeRequest(approved=True),
        )

        with self.assertRaises(WorkflowStateError):
            resume_priority_change_workflow(
                self.graph,
                "thread-001",
                PriorityChangeResumeRequest(approved=True),
            )

    def test_fastapi_start_and_resume_use_the_same_graph(self):
        with patch("main.PRIORITY_CHANGE_GRAPH", self.graph):
            with TestClient(app) as client:
                started = client.post("/workflows/priority-changes", json={
                    "thread_id": "thread-api-001",
                    "ticket_id": "T-1003",
                    "new_priority": "high",
                })
                resumed = client.post(
                    "/workflows/priority-changes/thread-api-001/resume",
                    json={"approved": True},
                )

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "WAITING_CONFIRMATION")
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["status"], "COMPLETED")
        self.assertEqual(query_ticket("T-1003")["priority"], "high")

    def test_fastapi_unknown_thread_returns_json_404(self):
        with TestClient(app) as client:
            response = client.post(
                "/workflows/priority-changes/thread-does-not-exist/resume",
                json={"approved": True},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {
            "detail": "工作流不存在，或该 thread_id 从未启动。",
        })


if __name__ == "__main__":
    unittest.main()
