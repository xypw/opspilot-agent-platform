"""聊天接口测试：默认模式全链路离线，真实模式的外部调用使用替身。"""

import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from main import app
from preview_tool_call import ModelAPIError


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_mock_default_runs_local_query_without_key_or_network(self):
        with patch("chat_service.load_api_key") as key_loader:
            with patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("不允许出网")) as network:
                response = self.client.post("/chat", json={"message": "帮我查工单 T-1003"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["mode"], "mock")
        self.assertTrue(data["is_mock"])
        self.assertEqual(data["model"], "mock")
        self.assertEqual(data["model_requests"], 0)
        self.assertEqual(data["simulated_model_requests"], 2)
        self.assertIn("[模拟模式]", data["answer"])
        self.assertEqual(data["tool_result"], {"id": "T-1003", "status": "open", "priority": "medium"})
        key_loader.assert_not_called()
        network.assert_not_called()

    def test_other_ticket_is_not_hardcoded_and_requests_are_isolated(self):
        for ticket_id, status in [("T-1001", "open"), ("T-1002", "closed")]:
            with self.subTest(ticket_id=ticket_id):
                response = self.client.post("/chat", json={"message": f"查 {ticket_id}", "mode": "mock"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["tool_result"]["id"], ticket_id)
                self.assertEqual(response.json()["tool_result"]["status"], status)
                self.assertEqual(response.json()["simulated_model_requests"], 2)

    def test_not_found_is_a_successful_chat_with_no_ticket(self):
        response = self.client.post("/chat", json={"message": "查 T-9999"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["tool_result"])
        self.assertIn("未找到", response.json()["answer"])

    def test_mock_requires_exactly_one_well_formed_id(self):
        for message in ["你好", "查 T-1001 和 T-1002", "查 T-10030", "查 T-1003-extra",
                        "查 O-20030", "查 O-2003-extra", "查 O-2001 和 O-2002", "查 T-1003 和 O-2003"]:
            with self.subTest(message=message):
                response = self.client.post("/chat", json={"message": message})
                self.assertEqual(response.status_code, 400)

    def test_mock_order_query_uses_order_function_without_network_or_key(self):
        with patch("chat_service.load_api_key") as key_loader, \
             patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("不允许出网")) as network, \
             patch("tool_executor.query_ticket") as ticket_query:
            response = self.client.post("/chat", json={"message": "帮我查订单 O-2003", "mode": "mock"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tool_name"], "query_order")
        self.assertEqual(data["tool_result"], {"id": "O-2003", "status": "cancelled", "product": "显示器"})
        self.assertIn("显示器", data["answer"])
        self.assertIn("cancelled", data["answer"])
        self.assertIn("[模拟模式]", data["answer"])
        self.assertEqual(data["mode"], "mock")
        self.assertTrue(data["is_mock"])
        self.assertEqual(data["model"], "mock")
        self.assertEqual(data["model_requests"], 0)
        self.assertEqual(data["simulated_model_requests"], 2)
        ticket_query.assert_not_called()
        key_loader.assert_not_called()
        network.assert_not_called()

    def test_ticket_and_order_requests_do_not_leak_state(self):
        for record_id, tool_name, expected_status in [
            ("O-2001", "query_order", "shipped"),
            ("T-1002", "query_ticket", "closed"),
            ("O-2002", "query_order", "processing"),
        ]:
            with self.subTest(record_id=record_id):
                response = self.client.post("/chat", json={"message": f"查 {record_id}"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["tool_name"], tool_name)
                self.assertEqual(data["tool_result"]["id"], record_id)
                self.assertEqual(data["tool_result"]["status"], expected_status)
                self.assertEqual(data["simulated_model_requests"], 2)

    def test_missing_order_does_not_invent_a_record(self):
        response = self.client.post("/chat", json={"message": "查 O-9999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_name"], "query_order")
        self.assertIsNone(response.json()["tool_result"])
        self.assertIn("未找到订单 O-9999", response.json()["answer"])

    def test_repeating_same_order_id_is_still_one_query(self):
        response = self.client.post("/chat", json={"message": "查 O-2003，就是订单 O-2003"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_result"]["id"], "O-2003")
        self.assertEqual(response.json()["simulated_model_requests"], 2)

    def test_live_order_path_with_offline_model_responses(self):
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "offline_order_call", "type": "function", "function": {
                        "name": "query_order", "arguments": '{"order_id":"O-2002"}',
                    },
                }]}
            else:
                self.assertEqual(len(requests), 2, "不能追加第三次请求")
                result = json.loads(payload["messages"][-1]["content"])
                self.assertEqual(result["id"], "O-2002")
                message = {"role": "assistant", "content": "无线鼠标订单正在处理中。"}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        # 测试 live 控制路径，但网络被替身接管，不代表真实模型验收。
        model_client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        self.addCleanup(model_client.close)
        with patch("chat_service.load_api_key", return_value="fake-test-key"), \
             patch("chat_service.httpx.Client", return_value=model_client), \
             patch("chat_service.run_mock_chat") as fallback:
            response = self.client.post("/chat", json={"message": "查订单 O-2002", "mode": "live"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tool_name"], "query_order")
        self.assertEqual(data["tool_result"]["product"], "无线鼠标")
        self.assertFalse(data["is_mock"])
        self.assertEqual(data["model_requests"], 2)
        self.assertEqual(data["simulated_model_requests"], 0)
        self.assertEqual(len(requests), 2)
        for payload in requests:
            self.assertEqual(payload["messages"][1]["content"], "查订单 O-2002")
        self.assertNotIn("tools", requests[1])
        fallback.assert_not_called()

    def test_invalid_request_returns_422_before_service(self):
        for body in [{}, {"message": None}, {"message": "   "}, {"message": 123},
                     {"message": "x" * 1001}, {"message": "查 T-1003", "mode": "paid"},
                     {"message": "查 T-1003", "api_key": "fake"}]:
            with self.subTest(body_type=type(body)):
                with patch("main.chat") as service:
                    response = self.client.post("/chat", json=body)
                self.assertEqual(response.status_code, 422)
                service.assert_not_called()

    def test_live_passes_user_message_and_filters_response_fields(self):
        trace = {
            "model": "glm-4.7-flash", "question": "查 T-1002", "tool_name": "query_ticket",
            "tool_result": {"id": "T-1002", "status": "closed", "priority": "low"},
            "answer": "工单已关闭。", "model_requests": 2,
            "arguments_json": '{"ticket_id":"T-1002"}',
        }
        with patch("chat_service.load_api_key", return_value="fake-test-key"):
            with patch("chat_service.run_roundtrip", return_value=trace) as agent:
                response = self.client.post("/chat", json={"message": "  查 T-1002  ", "mode": "live"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(agent.call_args.kwargs["question"], "查 T-1002")
        self.assertFalse(response.json()["is_mock"])
        self.assertEqual(response.json()["model_requests"], 2)
        self.assertEqual(response.json()["simulated_model_requests"], 0)
        self.assertNotIn("arguments_json", response.json())
        self.assertNotIn("fake-test-key", response.text)

    def test_live_missing_key_returns_503(self):
        with patch("chat_service.load_api_key", side_effect=ValueError("fake-secret")):
            with patch("chat_service.run_roundtrip") as agent:
                response = self.client.post("/chat", json={"message": "查 T-1003", "mode": "live"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("fake-secret", response.text)
        agent.assert_not_called()

    def test_live_failure_never_falls_back_to_mock(self):
        with patch("chat_service.run_live_chat", side_effect=ModelAPIError(429, "1305")):
            with patch("chat_service.run_mock_chat") as mock_service:
                response = self.client.post("/chat", json={"message": "查 T-1003", "mode": "live"})
        self.assertEqual(response.status_code, 503)
        mock_service.assert_not_called()

    def test_errors_map_to_safe_http_responses(self):
        for error, status in [
            (ModelAPIError(401), 503), (ModelAPIError(500), 502),
            (httpx.ReadTimeout("fake-secret"), 504),
            (httpx.ConnectError("fake-secret"), 502),
            (ValueError("fake-secret"), 502),
        ]:
            with self.subTest(error=type(error).__name__, status=status):
                with patch("main.chat", side_effect=error):
                    response = self.client.post("/chat", json={"message": "查 T-1003", "mode": "live"})
                self.assertEqual(response.status_code, status)
                self.assertNotIn("fake-secret", response.text)

    def test_docs_describe_request_body_and_response(self):
        schema = self.client.get("/openapi.json").json()
        chat = schema["paths"]["/chat"]["post"]
        self.assertIn("application/json", chat["requestBody"]["content"])
        self.assertIn("422", chat["responses"])
        self.assertIn("ChatResponse", schema["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
