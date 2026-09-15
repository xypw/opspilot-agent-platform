"""使用模拟 HTTP 响应测试，不读取真实密钥，不访问模型服务。"""

import json
import unittest
from unittest.mock import patch

import httpx

from preview_tool_call import (
    API_URL, build_initial_messages, extract_tool_preview, load_api_key, request_tool_call,
)
from tool_schema import (
    CHECK_RETURN_ELIGIBILITY_TOOL,
    QUERY_ORDER_TOOL,
    QUERY_TICKET_TOOL,
    REQUEST_PRIORITY_CHANGE_TOOL,
    SEARCH_KNOWLEDGE_BASE_TOOL,
)


def sample_message():
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "demo-call-id",
            "type": "function",
            "function": {"name": "query_ticket", "arguments": '{"ticket_id":"T-1003"}'},
        }],
    }


class ToolPreviewTests(unittest.TestCase):
    def test_general_refund_question_routes_to_knowledge_without_order_id(self):
        messages = build_initial_messages("我应该怎么退款")
        instruction = messages[0]["content"]

        self.assertIn("必须先使用 search_knowledge_base，不需要订单编号", instruction)
        self.assertIn("只有查询某个具体订单、工单或退货资格", instruction)
        self.assertEqual(messages[1], {"role": "user", "content": "我应该怎么退款"})

    def test_schema_requires_string_ticket_id(self):
        parameters = QUERY_TICKET_TOOL["function"]["parameters"]
        self.assertEqual(parameters["required"], ["ticket_id"])
        self.assertEqual(parameters["properties"]["ticket_id"]["type"], "string")
        self.assertFalse(parameters["additionalProperties"])

    def test_request_uses_only_fixed_model_and_official_url(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), API_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer fake-test-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "glm-4.7-flash")
            self.assertEqual(
                payload["tools"],
                [QUERY_TICKET_TOOL, QUERY_ORDER_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
                 SEARCH_KNOWLEDGE_BASE_TOOL,
                 REQUEST_PRIORITY_CHANGE_TOOL],
            )
            self.assertEqual(payload["tool_choice"], "auto")
            self.assertNotIn("fake-test-key", request.content.decode())
            return httpx.Response(200, json={"choices": [{"message": sample_message()}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            message = request_tool_call("fake-test-key", client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(extract_tool_preview(message)["arguments"], '{"ticket_id":"T-1003"}')

    def test_http_errors_do_not_expose_body_or_retry(self):
        for status in [302, 401, 429, 500]:
            with self.subTest(status=status):
                requests = []

                def handler(request):
                    requests.append(request)
                    return httpx.Response(status, text="fake-test-key", headers={"Location": "https://example.com"})

                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(ValueError) as caught:
                        request_tool_call("fake-test-key", client)
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn("fake-test-key", str(caught.exception))
                self.assertEqual(len(requests), 1)

    def test_malformed_response_is_rejected(self):
        for payload in [{}, {"choices": []}, {"choices": [{"message": None}]}]:
            with self.subTest(payload=payload):
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
                    with self.assertRaises(ValueError):
                        request_tool_call("fake-test-key", client)

    def test_numeric_provider_code_is_safe_to_display(self):
        payload = {"error": {"code": "1305", "message": "fake-test-key"}}
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json=payload))) as client:
            with self.assertRaises(ValueError) as caught:
                request_tool_call("fake-test-key", client)
        self.assertIn("1305", str(caught.exception))
        self.assertNotIn("fake-test-key", str(caught.exception))

    def test_missing_or_multiple_calls_are_rejected(self):
        for calls in [None, [], [sample_message()["tool_calls"][0]] * 2]:
            with self.subTest(calls=calls):
                with self.assertRaises(ValueError):
                    extract_tool_preview({"tool_calls": calls})

    def test_unknown_tool_is_rejected(self):
        message = sample_message()
        message["tool_calls"][0]["function"]["name"] = "delete_ticket"
        with self.assertRaises(ValueError):
            extract_tool_preview(message)

    def test_order_tool_preserves_name_and_arguments(self):
        message = sample_message()
        function = {"name": "query_order", "arguments": '{"order_id":"O-2003"}'}
        message["tool_calls"][0]["function"] = function
        self.assertEqual(extract_tool_preview(message), function)

    def test_priority_change_request_is_an_allowed_tool(self):
        message = sample_message()
        function = {
            "name": "request_priority_change",
            "arguments": '{"ticket_id":"T-1003","new_priority":"high"}',
        }
        message["tool_calls"][0]["function"] = function
        self.assertEqual(extract_tool_preview(message), function)

    def test_return_eligibility_is_an_allowed_tool(self):
        message = sample_message()
        function = {
            "name": "check_return_eligibility",
            "arguments": '{"order_id":"O-2001"}',
        }
        message["tool_calls"][0]["function"] = function
        self.assertEqual(extract_tool_preview(message), function)

    def test_knowledge_search_is_an_allowed_tool(self):
        message = sample_message()
        function = {
            "name": "search_knowledge_base",
            "arguments": '{"query":"退款多久到账","limit":2}',
        }
        message["tool_calls"][0]["function"] = function
        self.assertEqual(extract_tool_preview(message), function)

    def test_invalid_tool_name_types_are_rejected(self):
        for name in [None, [], {}, 123]:
            with self.subTest(name=name):
                message = sample_message()
                message["tool_calls"][0]["function"]["name"] = name
                with self.assertRaises(ValueError):
                    extract_tool_preview(message)

    def test_arguments_must_be_text(self):
        message = sample_message()
        message["tool_calls"][0]["function"]["arguments"] = {"ticket_id": "T-1003"}
        with self.assertRaises(ValueError):
            extract_tool_preview(message)

    @patch("preview_tool_call.dotenv_values", return_value={"ZHIPU_API_KEY": ""})
    def test_empty_local_key_is_rejected(self, read_env):
        with self.assertRaises(ValueError):
            load_api_key()
        self.assertFalse(read_env.call_args.kwargs["interpolate"])


if __name__ == "__main__":
    unittest.main()
