"""通过真实 FastAPI/LangGraph 验证暂停恢复；Java 查询使用固定响应替身。"""

import unittest
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient

from agent_graph import ConfiguredAgentModelGateway, build_agent_graph
from main import app
from pending_actions import PendingActionStore
from order_service_client import OrderServiceError
from return_draft_client import ReturnDraftExpired, ReturnOrderChanged, JavaReturnDraftGateway
import httpx


class ReturnReasonFlowTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = Mock(return_value={"order_id": "O-2001", "decision": "MANUAL_REVIEW",
                                          "reason": "EVIDENCE_REVIEW_REQUIRED"})
        self.draft = {"draft_id": "00000000-0000-0000-0000-000000000001", "order_id": "O-2001",
                      "product": "机械键盘", "amount_cents": 39900,
                      "started_at": "2026-09-07T15:30:00Z", "expires_at": "2026-09-07T16:30:00Z",
                      "status": "WAITING_REASON"}
        self.application = {
            "application_id": "00000000-0000-0000-0000-000000000002",
            "order_id": "O-2001", "product": "机械键盘", "refund_amount_cents": 39900,
            "status": "SUBMITTED", "created_at": "2026-09-07T15:35:00Z",
        }
        self.gateway = Mock()
        self.gateway.start.return_value = self.draft
        self.gateway.get.return_value = self.draft
        self.gateway.submit = self.reviewer
        self.gateway.confirm.return_value = self.application
        self.gateway.cancel.return_value = {**self.draft, "status": "CANCELLED"}
        self.graph = build_agent_graph(PendingActionStore(), ConfiguredAgentModelGateway(),
                                       draft_gateway=self.gateway)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        graph_patch = patch("main.AGENT_GRAPH", self.graph)
        graph_patch.start()
        self.addCleanup(graph_patch.stop)
        gateway_patch = patch("main.RETURN_DRAFT_GATEWAY", self.gateway)
        gateway_patch.start()
        self.addCleanup(gateway_patch.stop)
        query_patch = patch("tool_executor.RETURN_ELIGIBILITY_QUERY", return_value={
            "order_id": "O-2001", "decision": "REASON_REQUIRED", "can_apply": True,
            "reason_required": True, "days_since_delivery": 10,
            "reason": "WITHIN_15_DAY_CONDITIONAL_WINDOW",
        })
        self.query = query_patch.start()
        self.addCleanup(query_patch.stop)

    def start(self, thread="reason-1", **extra):
        return self.client.post("/agent-graph/runs", json={
            "thread_id": thread, "message": "订单 O-2001 能退货吗", "mode": "mock", **extra,
        })

    def test_pause_refresh_resume_and_repeated_submission(self):
        waiting = self.start()
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json()["status"], "WAITING_REASON")
        self.assertEqual(self.client.get("/agent-graph/runs/reason-1").json(), waiting.json())
        self.assertEqual(self.start().status_code, 409)
        response = self.client.post("/agent-graph/runs/reason-1/return-reason",
                                    json={"return_reason": "  按键失灵  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["return_reason"], "按键失灵")
        self.assertEqual(response.json()["order_id"], "O-2001")
        self.assertEqual(response.json()["status"], "COMPLETED")
        self.assertIsNone(response.json()["action_id"])
        self.assertIn("尚未创建", response.json()["answer"])
        self.query.assert_called_once_with("O-2001")
        self.reviewer.assert_called_once_with("O-2001", self.draft["draft_id"], "按键失灵", "OTHER")
        self.assertEqual(response.json()["return_review"]["decision"], "MANUAL_REVIEW")
        self.assertEqual(self.client.post("/agent-graph/runs/reason-1/return-reason",
                         json={"return_reason": "改一下"}).status_code, 409)

    def test_existing_reason_skips_question(self):
        response = self.start(return_reason="按键失灵")
        self.assertEqual(response.json()["status"], "COMPLETED")
        self.assertEqual(response.json()["return_reason"], "按键失灵")

    def test_expiry_is_visible_and_java_blocks_submission(self):
        self.start()
        self.gateway.get.return_value = {**self.draft, "status": "EXPIRED"}
        read = self.client.get("/agent-graph/runs/reason-1").json()
        self.assertEqual(read["status"], "EXPIRED")
        self.reviewer.side_effect = ReturnDraftExpired("过期")
        response = self.client.post("/agent-graph/runs/reason-1/return-reason",
                                    json={"return_reason": "按键失灵"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "EXPIRED")
        self.assertIsNone(response.json()["return_review"])
        self.assertEqual(self.client.get("/agent-graph/runs/reason-1").json()["status"], "EXPIRED")
        self.gateway.start.assert_called_once_with("O-2001")

    def test_repeated_start_does_not_replace_draft_deadline(self):
        first = self.start()
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.client.get("/agent-graph/runs/reason-1").json()["return_draft"],
                         first.json()["return_draft"])
        self.gateway.start.assert_called_once()

    def test_java_rejection_is_not_reported_as_successful_application(self):
        self.reviewer.return_value = {"order_id": "O-2001", "decision": "REJECTED",
                                     "reason": "PERSONAL_REASON_OUTSIDE_WINDOW"}
        response = self.start(return_reason="不喜欢", reason_code="PERSONAL_PREFERENCE")
        self.assertEqual(response.status_code, 200)
        self.assertIn("不可受理", response.json()["answer"])
        self.assertIsNone(response.json()["action_id"])

    def test_failed_review_retries_saved_node_without_repeating_query(self):
        self.start()
        self.reviewer.side_effect = [OrderServiceError("预审失败"), self.reviewer.return_value]
        payload = {"return_reason": "按键失灵", "reason_code": "QUALITY_ISSUE"}
        first = self.client.post("/agent-graph/runs/reason-1/return-reason", json=payload)
        self.assertEqual(first.status_code, 503)
        changed = self.client.post("/agent-graph/runs/reason-1/return-reason",
                                   json={"return_reason": "变更原因"})
        self.assertEqual(changed.status_code, 409)
        retried = self.client.post("/agent-graph/runs/reason-1/return-reason", json=payload)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(self.reviewer.call_count, 2)
        self.query.assert_called_once_with("O-2001")

    def test_invalid_reason_does_not_consume_interrupt(self):
        self.start()
        for payload in ({}, {"return_reason": "   "}, {"return_reason": 1},
                        {"return_reason": "故障", "order_id": "O-2002"}):
            with self.subTest(payload=payload):
                response = self.client.post("/agent-graph/runs/reason-1/return-reason", json=payload)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/agent-graph/runs/reason-1").json()["status"], "WAITING_REASON")
        self.assertEqual(self.client.post("/agent-graph/runs/reason-1/resume",
                         json={"approved": True}).status_code, 409)

    def test_threads_are_isolated_and_unknown_thread_rejected(self):
        self.start("reason-1")
        self.start("reason-2")
        self.client.post("/agent-graph/runs/reason-1/return-reason", json={"return_reason": "损坏"})
        other = self.client.get("/agent-graph/runs/reason-2").json()
        self.assertEqual(other["status"], "WAITING_REASON")
        self.assertIsNone(other["return_reason"])
        self.assertEqual(self.client.post("/agent-graph/runs/missing/return-reason",
                         json={"return_reason": "损坏"}).status_code, 404)

    def test_no_reason_return_waits_for_confirmation_then_creates_once(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }

        waiting = self.start(thread="confirm-1")
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json()["status"], "WAITING_CONFIRMATION")
        self.assertEqual(waiting.json()["return_draft"]["product"], "机械键盘")
        self.assertEqual(waiting.json()["return_draft"]["amount_cents"], 39900)
        self.assertIsNone(waiting.json()["return_application"])
        self.reviewer.assert_not_called()

        completed = self.client.post(
            "/agent-graph/runs/confirm-1/resume", json={"approved": True}
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "COMPLETED")
        self.assertEqual(completed.json()["return_application"]["status"], "SUBMITTED")
        self.assertEqual(completed.json()["return_application"]["refund_amount_cents"], 39900)
        self.gateway.confirm.assert_called_once_with("O-2001", self.draft["draft_id"])

        repeated = self.client.post(
            "/agent-graph/runs/confirm-1/resume", json={"approved": True}
        )
        self.assertEqual(repeated.status_code, 409)
        self.gateway.confirm.assert_called_once()

    def test_declining_confirmation_cancels_without_creating_application(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        self.start(thread="cancel-1")

        cancelled = self.client.post(
            "/agent-graph/runs/cancel-1/resume", json={"approved": False}
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertIsNone(cancelled.json()["return_application"])
        self.gateway.cancel.assert_called_once_with("O-2001", self.draft["draft_id"])
        self.gateway.confirm.assert_not_called()

    def test_expiry_during_confirmation_does_not_create_application(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        self.gateway.confirm.side_effect = ReturnDraftExpired("过期")
        self.start(thread="expired-confirm-1")

        expired = self.client.post(
            "/agent-graph/runs/expired-confirm-1/resume", json={"approved": True}
        )

        self.assertEqual(expired.status_code, 200)
        self.assertEqual(expired.json()["status"], "EXPIRED")
        self.assertIsNone(expired.json()["return_application"])
        self.assertIn("未创建", expired.json()["answer"])

    def test_java_conflict_reaches_fastapi_as_409_not_503(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        # 保留真实网关解析逻辑，只替换 Java HTTP 传输。
        self.gateway.confirm.side_effect = JavaReturnDraftGateway("http://java.test").confirm
        self.start(thread="changed-order")
        http = httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(
            lambda request: httpx.Response(409, json={"code": "ORDER_CHANGED"})))
        with patch("return_draft_client.httpx.Client", return_value=http):
            response = self.client.post("/agent-graph/runs/changed-order/resume",
                                        json={"approved": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "ORDER_CHANGED")
        self.assertIn("本次未创建", response.json()["detail"]["message"])
        snapshot = self.graph.get_state({"configurable": {"thread_id": "changed-order"}})
        self.assertEqual(snapshot.values["status"], "STALE_CONFIRMATION")
        self.assertIsNone(snapshot.values.get("return_application"))
        restored = self.client.get("/agent-graph/runs/changed-order")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["status"], "STALE_CONFIRMATION")
        self.assertIn("重新确认", restored.json()["answer"])
        self.assertEqual(self.client.post(
            "/agent-graph/runs/changed-order/resume", json={"approved": True}
        ).status_code, 409)

        new_draft = {
            **self.draft,
            "draft_id": "00000000-0000-0000-0000-000000000003",
            "amount_cents": 49900,
        }
        new_application = {
            **self.application,
            "application_id": "00000000-0000-0000-0000-000000000004",
            "refund_amount_cents": 49900,
        }
        self.gateway.refresh.return_value = new_draft
        self.gateway.confirm.reset_mock(side_effect=True, return_value=True)
        self.gateway.confirm.return_value = new_application

        refreshed = self.client.post(
            "/agent-graph/runs/changed-order/refresh-return-confirmation"
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["status"], "WAITING_CONFIRMATION")
        self.assertEqual(refreshed.json()["return_draft"]["draft_id"], new_draft["draft_id"])
        self.assertEqual(refreshed.json()["return_draft"]["amount_cents"], 49900)
        self.assertIsNone(refreshed.json()["return_application"])
        self.gateway.refresh.assert_called_once_with("O-2001", self.draft["draft_id"])
        self.assertEqual(self.query.call_count, 2)

        completed = self.client.post(
            "/agent-graph/runs/changed-order/resume", json={"approved": True}
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["return_application"]["refund_amount_cents"], 49900)
        self.gateway.confirm.assert_called_once_with("O-2001", new_draft["draft_id"])

    def test_confirm_service_failure_remains_503(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        self.gateway.confirm.side_effect = OrderServiceError("服务暂时不可用")
        self.start(thread="service-failure")
        response = self.client.post("/agent-graph/runs/service-failure/resume",
                                    json={"approved": True})
        self.assertEqual(response.status_code, 503)

    def test_refresh_rechecks_eligibility_and_can_require_reason(self):
        self.query.return_value = {
            "order_id": "O-2001", "decision": "NO_REASON_ALLOWED", "can_apply": True,
            "reason_required": False, "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        self.gateway.confirm.side_effect = ReturnOrderChanged()
        self.start(thread="window-changed")
        self.assertEqual(self.client.post(
            "/agent-graph/runs/window-changed/resume", json={"approved": True}
        ).status_code, 409)

        self.query.return_value = {
            "order_id": "O-2001", "decision": "REASON_REQUIRED", "can_apply": True,
            "reason_required": True, "days_since_delivery": 8,
            "reason": "WITHIN_15_DAY_CONDITIONAL_WINDOW",
        }
        self.gateway.refresh.return_value = {
            **self.draft,
            "draft_id": "00000000-0000-0000-0000-000000000005",
        }
        refreshed = self.client.post(
            "/agent-graph/runs/window-changed/refresh-return-confirmation"
        )

        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["status"], "WAITING_REASON")
        self.assertIsNone(refreshed.json()["return_reason"])
        self.assertIn("重新填写", refreshed.json()["answer"])
        self.assertEqual(self.client.post(
            "/agent-graph/runs/window-changed/resume", json={"approved": True}
        ).status_code, 409)
