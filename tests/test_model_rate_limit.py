"""准入单测及显式启用的本地 Redis 集成；不会请求模型供应商。"""
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import os
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import redis
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from model_rate_limit import (
    ACQUIRE_SCRIPT, ModelAdmissionUnavailable, ModelRateLimitExceeded,
    RedisModelRateLimiter, acquire_model_request,
)
from preview_tool_call import request_message
from retry_policy import ModelRequestTelemetry, request_message_with_retry


def _process_acquire(args):
    url, scope = args
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    try:
        limiter = RedisModelRateLimiter(client, scope=scope, limit=3, window_seconds=60)
        try:
            limiter.acquire()
            return True
        except ModelRateLimitExceeded:
            return False
    finally:
        client.close()


class ModelRateLimitTests(unittest.TestCase):
    def test_atomic_script_and_retry_after(self):
        client = Mock()
        client.eval.return_value = [0, 1001]
        limiter = RedisModelRateLimiter(client, scope="test-account", limit=2, window_seconds=60)
        with self.assertRaises(ModelRateLimitExceeded) as raised:
            limiter.acquire()
        self.assertEqual(raised.exception.retry_after, 2)
        client.eval.assert_called_once_with(ACQUIRE_SCRIPT, 1, limiter.key, 2, 60000)

    def test_redis_failure_never_falls_back_or_leaks_url(self):
        client = Mock()
        client.eval.side_effect = RedisConnectionError("redis://private:secret@host")
        limiter = RedisModelRateLimiter(client, scope="test-account", limit=2, window_seconds=60)
        with self.assertRaises(ModelAdmissionUnavailable) as raised:
            limiter.acquire()
        self.assertNotIn("secret", str(raised.exception))

    def test_invalid_configuration_is_closed(self):
        for config in ({"MODEL_RATE_LIMIT_REQUESTS": "bad"},
                       {"MODEL_RATE_LIMIT_REQUESTS": "2"},
                       {"MODEL_RATE_LIMIT_REQUESTS": "-1", "REDIS_URL": "redis://localhost"}):
            with self.subTest(config=config), patch.dict(os.environ, config, clear=True):
                with self.assertRaises(ModelAdmissionUnavailable):
                    acquire_model_request()

    def test_disabled_offline_mode_does_not_connect(self):
        with patch.dict(os.environ, {}, clear=True), patch("model_rate_limit._configured_limiter") as factory:
            acquire_model_request()
            factory.assert_not_called()

    def test_direct_request_is_also_guarded(self):
        client = Mock()
        with patch("preview_tool_call.acquire_model_request", side_effect=ModelRateLimitExceeded(2)):
            with self.assertRaises(ModelRateLimitExceeded):
                request_message("fake", client, [], offer_tools=False)
        client.post.assert_not_called()

    def test_denied_retry_is_not_counted_as_http(self):
        for admissions, expected_attempts in (([ModelRateLimitExceeded(3)], 0),
                                               ([None, ModelRateLimitExceeded(3)], 1)):
            telemetry = ModelRequestTelemetry()
            transport = Mock(return_value=httpx.Response(429, json={"error": {"code": "1305"}}))
            with self.subTest(attempts=expected_attempts), httpx.Client(
                transport=httpx.MockTransport(transport)
            ) as client, patch("preview_tool_call.acquire_model_request", side_effect=admissions):
                with self.assertRaises(ModelRateLimitExceeded):
                    request_message_with_retry("fake", client, [], sleeper=lambda _: None, telemetry=telemetry)
            self.assertEqual(transport.call_count, expected_attempts)
            self.assertEqual(telemetry.http_attempts, expected_attempts)
            self.assertEqual(telemetry.retry_count, 0)

    @patch.dict(os.environ, {"OPSPILOT_AUTH_REQUIRED": "false"})
    def test_api_distinguishes_quota_and_store_failure(self):
        from main import app
        for error, status, code in ((ModelRateLimitExceeded(8), 429, "MODEL_RATE_LIMITED"),
                                    (ModelAdmissionUnavailable(), 503, "MODEL_ADMISSION_UNAVAILABLE")):
            with self.subTest(code=code), patch("main.start_agent_graph", side_effect=error), TestClient(app) as client:
                response = client.post("/agent-graph/runs", json={
                    "thread_id": "quota-test", "message": "退款多久到账？", "mode": "mock",
                })
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json()["code"], code)
            if status == 429:
                self.assertEqual(response.headers["Retry-After"], "8")


@unittest.skipUnless(os.getenv("RUN_REDIS_RATE_LIMIT_INTEGRATION") == "1", "需显式启用本地 Redis 集成")
class RedisRateLimitIntegrationTests(unittest.TestCase):
    def setUp(self):
        url = self.url = os.getenv("RATE_LIMIT_TEST_REDIS_URL", "redis://127.0.0.1:6380/0")
        self.clients = [redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) for _ in range(2)]
        scope = self.scope = "integration-" + uuid4().hex
        self.limiters = [RedisModelRateLimiter(c, scope=scope, limit=3, window_seconds=60) for c in self.clients]
        self.key = self.limiters[0].key
        for client in self.clients:
            self.addCleanup(client.close)
        # 服务不可达时在任何写入之前失败，不再触发无意义的清理连接错误。
        self.clients[0].ping()
        # 仅删除本测试 UUID 对应的 key；不扫描、不清空任何现有数据。
        self.addCleanup(self.clients[0].delete, self.key)

    def test_two_clients_share_atomic_budget_and_expiry(self):
        def attempt(index):
            try:
                self.limiters[index % 2].acquire()
                return True
            except ModelRateLimitExceeded:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(results), 3)
        self.assertEqual(int(self.clients[0].get(self.key)), 3)
        self.assertGreater(self.clients[0].pttl(self.key), 0)
        self.assertLessEqual(self.clients[0].pttl(self.key), 60000)
        # 精确删除自己创建的窗口 key 模拟过期，不触碰业务 key。
        self.clients[0].delete(self.key)
        self.limiters[1].acquire()
        self.assertEqual(int(self.clients[0].get(self.key)), 1)

    def test_independent_processes_share_budget(self):
        with ProcessPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(_process_acquire, [(self.url, self.scope)] * 8))
        self.assertEqual(sum(admitted), 3)
        self.assertEqual(int(self.clients[0].get(self.key)), 3)

    def test_expired_window_recovers_and_rejection_does_not_extend_it(self):
        for _ in range(3):
            self.limiters[0].acquire()
        before = self.clients[0].pttl(self.key)
        with self.assertRaises(ModelRateLimitExceeded):
            self.limiters[1].acquire()
        self.assertLessEqual(self.clients[0].pttl(self.key), before)
        # 缩短仅本测试 key 的 TTL，验证 Redis 真正过期，而非手动删除代替过期。
        self.clients[0].pexpire(self.key, 20)
        deadline = monotonic() + 2
        while self.clients[0].exists(self.key) and monotonic() < deadline:
            sleep(0.01)
        self.assertFalse(self.clients[0].exists(self.key))
        self.limiters[1].acquire()
        self.assertEqual(int(self.clients[0].get(self.key)), 1)

    def test_existing_full_counter_without_ttl_remains_closed(self):
        self.clients[0].set(self.key, 3)
        with self.assertRaises(ModelRateLimitExceeded):
            self.limiters[0].acquire()
        self.assertEqual(int(self.clients[0].get(self.key)), 3)
        self.assertGreater(self.clients[0].pttl(self.key), 0)


if __name__ == "__main__":
    unittest.main()
