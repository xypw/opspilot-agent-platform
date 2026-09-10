import unittest
from unittest.mock import patch
import httpx
from return_draft_client import JavaReturnDraftGateway, ReturnDraftExpired, ReturnOrderChanged
from order_service_client import OrderServiceError


class ReturnDraftClientTests(unittest.TestCase):
    def setUp(self):
        self.gateway = JavaReturnDraftGateway("http://java.test")
        self.draft = {"draft_id": "00000000-0000-0000-0000-000000000001", "order_id": "O-2001",
                      "product": "机械键盘", "amount_cents": 39900,
                      "started_at": "2026-09-07T15:30:00Z", "expires_at": "2026-09-07T16:30:00Z",
                      "status": "WAITING_REASON"}

    def test_creation_cannot_send_client_timestamps(self):
        def handle(request):
            self.assertEqual(request.method, "POST")
            self.assertNotIn(b"started_at", request.content)
            self.assertEqual(request.url.path, "/api/orders/O-2001/return-draft")
            return httpx.Response(200, json=self.draft)
        http = httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handle))
        with patch("return_draft_client.httpx.Client", return_value=http):
            self.assertEqual(self.gateway.start("O-2001"), self.draft)

    def test_only_exact_order_changed_contract_is_business_conflict(self):
        for response, expected in [
            (httpx.Response(409, json={"code": "ORDER_CHANGED"}), ReturnOrderChanged),
            (httpx.Response(409, json={"code": "UNKNOWN"}), OrderServiceError),
            (httpx.Response(409, text="private upstream error"), OrderServiceError),
            (httpx.Response(500, json={"code": "ORDER_CHANGED"}), OrderServiceError),
        ]:
            with self.subTest(status=response.status_code, expected=expected):
                http = httpx.Client(base_url="http://java.test",
                    transport=httpx.MockTransport(lambda request: response))
                with patch("return_draft_client.httpx.Client", return_value=http):
                    with self.assertRaises(expected):
                        self.gateway.confirm("O-2001", self.draft["draft_id"])

    def test_expiry_is_distinct_from_service_failure(self):
        for response, exception in [
                (httpx.Response(410, json={"code": "DRAFT_EXPIRED"}), ReturnDraftExpired),
                (httpx.Response(500), OrderServiceError),
                (httpx.Response(410, text="not JSON"), OrderServiceError)]:
            with self.subTest(status=response.status_code):
                http = httpx.Client(base_url="http://java.test",
                                    transport=httpx.MockTransport(lambda r: response))
                with patch("return_draft_client.httpx.Client", return_value=http):
                    with self.assertRaises(exception):
                        self.gateway.submit("O-2001", self.draft["draft_id"], "故障", "QUALITY_ISSUE")

    def test_wrong_order_or_duration_rejected(self):
        for invalid in [{**self.draft, "order_id": "O-2002"},
                        {**self.draft, "expires_at": "2026-09-08T16:30:00Z"}]:
            http = httpx.Client(base_url="http://java.test",
                                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=invalid)))
            with patch("return_draft_client.httpx.Client", return_value=http):
                with self.assertRaises(OrderServiceError):
                    self.gateway.start("O-2001")

    def test_confirm_cancel_and_refresh_use_distinct_business_endpoints(self):
        application = {
            "application_id": "00000000-0000-0000-0000-000000000002",
            "order_id": "O-2001",
            "product": "机械键盘",
            "refund_amount_cents": 39900,
            "status": "SUBMITTED",
            "created_at": "2026-09-07T15:35:00Z",
        }

        def handle(request):
            if request.url.path.endswith("/confirm"):
                return httpx.Response(200, json=application)
            if request.url.path.endswith("/cancel"):
                return httpx.Response(200, json={**self.draft, "status": "CANCELLED"})
            if request.url.path.endswith("/refresh"):
                return httpx.Response(200, json={
                    **self.draft,
                    "draft_id": "00000000-0000-0000-0000-000000000003",
                    "amount_cents": 49900,
                })
            return httpx.Response(404)

        confirm_http = httpx.Client(
            base_url="http://java.test", transport=httpx.MockTransport(handle)
        )
        cancel_http = httpx.Client(
            base_url="http://java.test", transport=httpx.MockTransport(handle)
        )
        refresh_http = httpx.Client(
            base_url="http://java.test", transport=httpx.MockTransport(handle)
        )
        with patch(
            "return_draft_client.httpx.Client",
            side_effect=[confirm_http, cancel_http, refresh_http],
        ):
            confirmed = self.gateway.confirm("O-2001", self.draft["draft_id"])
            cancelled = self.gateway.cancel("O-2001", self.draft["draft_id"])
            refreshed = self.gateway.refresh("O-2001", self.draft["draft_id"])

        self.assertEqual(confirmed, application)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(refreshed["amount_cents"], 49900)
