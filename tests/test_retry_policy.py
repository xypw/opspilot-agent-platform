"""受控重试测试：全部通过 MockTransport 执行，不访问外网。"""

import json
import unittest

import httpx

from preview_tool_call import API_URL, ModelAPIError, build_initial_messages
from retry_policy import is_retryable_error, request_message_with_retry


class RetryPolicyTests(unittest.TestCase):
    def make_client(self, statuses: list[int]):
        request_count = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            self.assertEqual(str(request.url), API_URL)
            self.assertEqual(json.loads(request.content)["model"], "glm-4.7-flash")
            status = statuses[request_count]
            request_count += 1
            if status == 200:
                return httpx.Response(200, json={
                    "choices": [{"message": {"role": "assistant", "content": "成功"}}]
                })
            return httpx.Response(status, json={"error": {"code": "1305"}})

        client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        self.addCleanup(client.close)
        return client, lambda: request_count

    def test_429_retries_with_exponential_backoff(self):
        client, request_count = self.make_client([429, 503, 200])
        delays = []

        result = request_message_with_retry(
            "fake-key", client, build_initial_messages(), sleeper=delays.append
        )

        self.assertEqual(result["content"], "成功")
        self.assertEqual(request_count(), 3)
        self.assertEqual(delays, [0.2, 0.4])

    def test_400_does_not_retry(self):
        client, request_count = self.make_client([400])
        delays = []

        with self.assertRaises(ModelAPIError):
            request_message_with_retry(
                "fake-key", client, build_initial_messages(), sleeper=delays.append
            )
        self.assertEqual(request_count(), 1)
        self.assertEqual(delays, [])

    def test_request_error_retries(self):
        request_count = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                raise httpx.ReadTimeout("模拟超时", request=request)
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "成功"}}]
            })

        client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        self.addCleanup(client.close)
        delays = []

        result = request_message_with_retry(
            "fake-key", client, build_initial_messages(), sleeper=delays.append
        )
        self.assertEqual(result["content"], "成功")
        self.assertEqual(request_count, 2)
        self.assertEqual(delays, [0.2])

    def test_invalid_model_response_is_not_retryable(self):
        self.assertFalse(is_retryable_error(ValueError("响应格式错误")))
        self.assertFalse(is_retryable_error(ModelAPIError(401)))
        self.assertTrue(is_retryable_error(ModelAPIError(429)))

    def test_invalid_attempt_count_fails_before_request(self):
        client, request_count = self.make_client([])
        with self.assertRaises(ValueError):
            request_message_with_retry(
                "fake-key", client, build_initial_messages(), max_attempts=0
            )
        self.assertEqual(request_count(), 0)


if __name__ == "__main__":
    unittest.main()
