"""显式售后入口的权限、证据失败关闭与确认快照契约。"""

import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_draft_client import JavaReturnDraftGateway, ReturnDraftRejected
from service_api import build_service_router


DRAFT = {
    "draft_id": "00000000-0000-0000-0000-000000000001",
    "order_id": "O-2001", "product": "机械键盘", "amount_cents": 39900,
    "started_at": "2026-09-23T08:00:00Z", "expires_at": "2026-09-23T09:00:00Z",
    "status": "WAITING_REASON",
}
EVIDENCE = {
    "chunk_id": "policy-1", "title": "售后政策", "page": 2,
    "content": "签收七日内可申请退货。", "score": 0.9,
}


class FakeGateway:
    def __init__(self):
        self.confirm_calls = 0

    def get(self, order_id, draft_id):
        return DRAFT

    def confirm(self, order_id, draft_id):
        self.confirm_calls += 1
        return {"order_id": order_id, "status": "SUBMITTED"}

    def start(self, order_id):
        return DRAFT


class ServiceApiTests(unittest.TestCase):
    def setUp(self):
        self.auth = patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"})
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.gateway = FakeGateway()
        app = FastAPI()
        app.include_router(build_service_router(
            order_query=lambda order_id: {"id": order_id},
            eligibility_query=lambda order_id: {"order_id": order_id,
                                                "decision": "NO_REASON_ALLOWED"},
            draft_gateway=self.gateway,
            evidence_reviewer=lambda question, candidates: {},
        ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_rules_answer_contains_only_admitted_evidence_and_page(self):
        with patch("service_api.search_knowledge_candidates", return_value=[EVIDENCE]), patch(
            "service_api.partition_evidence_for_question",
            return_value={"admitted": [EVIDENCE], "uncertain": [], "rejected": []},
        ):
            response = self.client.post("/service/knowledge/answer", json={
                "query": "七天能退货吗", "review_mode": "rules",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUPPORTED")
        self.assertIn("售后政策 第2页", response.json()["answer"])

    def test_live_review_failure_does_not_return_unreviewed_candidate(self):
        app = FastAPI()
        def unavailable(question, candidates):
            raise ValueError("模型响应无效")
        app.include_router(build_service_router(
            order_query=lambda _: None, eligibility_query=lambda _: None,
            draft_gateway=self.gateway, evidence_reviewer=unavailable,
        ))
        with TestClient(app) as client, patch(
            "service_api.search_knowledge_candidates", return_value=[EVIDENCE]
        ), patch("service_api.partition_evidence_for_question", return_value={
            "admitted": [EVIDENCE], "uncertain": [], "rejected": [],
        }):
            response = client.post("/service/knowledge/answer", json={
                "query": "七天能退货吗", "review_mode": "live",
            })
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(EVIDENCE["content"], response.text)

    def test_confirmation_requires_current_displayed_snapshot(self):
        path = "/service/orders/O-2001/return-drafts/" + DRAFT["draft_id"] + "/confirm"
        stale = self.client.post(path, json={
            "product": "机械键盘", "amount_cents": 49900,
            "expires_at": DRAFT["expires_at"],
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.gateway.confirm_calls, 0)
        accepted = self.client.post(path, json={
            "product": DRAFT["product"], "amount_cents": DRAFT["amount_cents"],
            "expires_at": DRAFT["expires_at"],
        })
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.gateway.confirm_calls, 1)

    def test_java_business_rejection_preserves_conflict_status(self):
        gateway = JavaReturnDraftGateway("http://java.test")
        http = httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(
            lambda request: httpx.Response(409, json={"code": "ORDER_NOT_ELIGIBLE"})))
        with patch("return_draft_client.httpx.Client", return_value=http):
            with self.assertRaises(ReturnDraftRejected) as raised:
                gateway.start("O-2001")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "ORDER_NOT_ELIGIBLE")

    def test_explicit_business_route_requires_authentication(self):
        with patch.dict(os.environ, {
            "OPSPILOT_AUTH_REQUIRED": "true",
            "OPSPILOT_AUTH_TOKENS": json.dumps({
                "test-token-0000000000001": {"user_id": "U-1001", "roles": ["customer"]},
            }),
            "OPSPILOT_INTERNAL_SERVICE_TOKEN": "test-internal-token-0000001",
        }):
            response = self.client.get("/service/orders/O-2001")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
