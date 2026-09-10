"""订单查询后端选择测试：显式配置，不允许拼写错误时静默降级。"""

import unittest
from unittest.mock import patch

import httpx

from order_query_factory import (
    ConfiguredJavaOrderQuery,
    build_order_query,
    build_return_eligibility_query,
)


class OrderQueryFactoryTests(unittest.TestCase):
    def test_memory_keeps_the_existing_local_query(self):
        self.assertIsNone(build_order_query(" memory "))

    def test_java_builds_a_callable_with_normalized_url(self):
        query = build_order_query(" JAVA ", "http://127.0.0.1:8081/")
        self.assertIsInstance(query, ConfiguredJavaOrderQuery)
        self.assertEqual(query.service_url, "http://127.0.0.1:8081")

    def test_unknown_backend_fails_instead_of_using_fake_data(self):
        with self.assertRaisesRegex(ValueError, "memory 或 java"):
            build_order_query("jvaa")

    def test_unsafe_or_malformed_service_urls_are_rejected(self):
        for url in (
            "",
            "127.0.0.1:8081",
            "file:///tmp/orders",
            "http://user:password@127.0.0.1:8081",
            "http://127.0.0.1:8081/api/orders",
            "http://127.0.0.1:8081?target=other",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    build_order_query("java", url)

    def test_configured_query_calls_java_client_contract(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "id": "O-2001", "status": "delivered", "product": "机械键盘",
                    "delivered_at": "2026-08-31", "amount_cents": 39900,
                },
            )
        )
        http = httpx.Client(base_url="http://java.test", transport=transport)
        with patch("order_query_factory.httpx.Client", return_value=http):
            result = ConfiguredJavaOrderQuery("http://java.test")("O-2001")
        self.assertEqual(result["id"], "O-2001")
        self.assertEqual(result["delivered_at"], "2026-08-31")
        self.assertEqual(result["amount_cents"], 39900)

    def test_return_eligibility_query_uses_configured_java_service(self):
        eligibility = {
            "order_id": "O-2001",
            "decision": "REASON_REQUIRED",
            "can_apply": True,
            "reason_required": True,
            "days_since_delivery": 8,
            "reason": "WITHIN_15_DAY_CONDITIONAL_WINDOW",
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=eligibility))
        http = httpx.Client(base_url="http://java.test", transport=transport)
        with patch("order_query_factory.httpx.Client", return_value=http):
            result = build_return_eligibility_query("http://java.test")("O-2001")
        self.assertEqual(result, eligibility)


if __name__ == "__main__":
    unittest.main()
