"""无需启动 Java 的客户端边界测试；用替身响应模拟真实 HTTP 协议。"""

import unittest

import httpx

from order_service_client import JavaOrderClient, OrderServiceError


ORDER = {
    "id": "O-2001",
    "status": "delivered",
    "product": "机械键盘",
    "delivered_at": "2026-08-31",
    "amount_cents": 39900,
}

ELIGIBILITY = {
    "order_id": "O-2001",
    "decision": "NO_REASON_ALLOWED",
    "can_apply": True,
    "reason_required": False,
    "days_since_delivery": 7,
    "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
}


class JavaOrderClientTests(unittest.TestCase):
    def query(self, response, order_id="O-2001"):
        with httpx.Client(
            base_url="http://java.test", transport=httpx.MockTransport(lambda request: response)
        ) as http:
            return JavaOrderClient(http).get_by_id(order_id)

    def test_returns_valid_order_and_uses_requested_path(self):
        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/orders/O-2001")
            return httpx.Response(200, json=ORDER)

        with httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handler)) as http:
            self.assertEqual(JavaOrderClient(http).get_by_id("O-2001"), ORDER)

    def test_only_business_not_found_returns_none(self):
        response = httpx.Response(404, json={"code": "ORDER_NOT_FOUND"})
        self.assertIsNone(self.query(response, "O-9999"))

    def test_wrong_route_404_is_not_a_missing_order(self):
        for response in (httpx.Response(404, text="Not Found"), httpx.Response(404, json={"detail": "wrong route"})):
            with self.subTest(response=response):
                with self.assertRaises(OrderServiceError):
                    self.query(response)

    def test_http_failures_are_not_none_or_raw_error_body(self):
        for status in (302, 400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                with self.assertRaises(OrderServiceError) as raised:
                    self.query(httpx.Response(status, text="PRIVATE_UPSTREAM_DETAIL"))
                self.assertNotIn("PRIVATE_UPSTREAM_DETAIL", str(raised.exception))

    def test_timeout_and_connection_failure_are_explicit(self):
        for failure in (httpx.ReadTimeout, httpx.ConnectError):
            def handler(request):
                raise failure("PRIVATE_CONNECTION_DETAIL", request=request)

            with self.subTest(failure=failure):
                with httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handler)) as http:
                    with self.assertRaises(OrderServiceError) as raised:
                        JavaOrderClient(http).get_by_id("O-2001")
                    self.assertNotIn("PRIVATE_CONNECTION_DETAIL", str(raised.exception))

    def test_malformed_success_body_is_rejected(self):
        with self.assertRaises(OrderServiceError):
            self.query(httpx.Response(200, text="not JSON"))
        for payload in (
            None,
            [],
            {},
            {**ORDER, "status": "invented"},
            {**ORDER, "product": 123},
            {**ORDER, "delivered_at": "31-08-2026"},
            {**ORDER, "amount_cents": 399.0},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(OrderServiceError):
                    self.query(httpx.Response(200, json=payload))

    def test_mismatched_order_id_is_rejected(self):
        with self.assertRaises(OrderServiceError):
            self.query(httpx.Response(200, json={**ORDER, "id": "O-2002"}))

    def test_invalid_ids_never_make_a_network_request(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=ORDER)

        with httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handler)) as http:
            client = JavaOrderClient(http)
            for order_id in ("../admin", "T-1001", "O-2001/extra", "", None, 123, "O-2001\n"):
                with self.subTest(order_id=order_id):
                    with self.assertRaises(ValueError):
                        client.get_by_id(order_id)
        self.assertEqual(requests, [])

    def test_return_eligibility_uses_java_rule_endpoint(self):
        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/orders/O-2001/return-eligibility")
            return httpx.Response(200, json=ELIGIBILITY)

        with httpx.Client(base_url="http://java.test", transport=httpx.MockTransport(handler)) as http:
            result = JavaOrderClient(http).get_return_eligibility("O-2001")

        self.assertEqual(result, ELIGIBILITY)

    def test_contradictory_return_decision_is_rejected(self):
        contradictory = {**ELIGIBILITY, "can_apply": False}
        with httpx.Client(
            base_url="http://java.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=contradictory)),
        ) as http:
            with self.assertRaises(OrderServiceError):
                JavaOrderClient(http).get_return_eligibility("O-2001")

    def test_missing_order_eligibility_returns_none(self):
        with httpx.Client(
            base_url="http://java.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(404, json={"code": "ORDER_NOT_FOUND"})
            ),
        ) as http:
            self.assertIsNone(JavaOrderClient(http).get_return_eligibility("O-9999"))


if __name__ == "__main__":
    unittest.main()
