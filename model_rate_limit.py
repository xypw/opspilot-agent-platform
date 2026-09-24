"""跨进程共享的模型请求准入；不缓存回答，不参与业务事务。"""
import hashlib
import math
import os
from functools import lru_cache

import redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


class ModelAdmissionError(RuntimeError):
    """请求尚未发往供应商时被本地准入层拒绝。"""


class ModelRateLimitExceeded(ModelAdmissionError):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, retry_after)
        super().__init__("模型共享请求配额已用完，请稍后重试。")


class ModelAdmissionUnavailable(ModelAdmissionError):
    def __init__(self):
        super().__init__("模型请求准入暂不可用；未发送本次模型请求。")


# 首次请求开启固定窗口。检查、递增、设置过期在 Redis 内原子执行。
ACQUIRE_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('PTTL', KEYS[1])
if count >= tonumber(ARGV[1]) then
    if ttl < 0 then redis.call('PEXPIRE', KEYS[1], ARGV[2]); ttl = tonumber(ARGV[2]) end
    return {0, ttl}
end
redis.call('INCR', KEYS[1])
if ttl < 0 then redis.call('PEXPIRE', KEYS[1], ARGV[2]); ttl = tonumber(ARGV[2]) end
return {1, ttl}
"""


class RedisModelRateLimiter:
    def __init__(self, client, *, scope: str, limit: int, window_seconds: int):
        if (not scope.strip() or len(scope) > 128 or type(limit) is not int
                or not 1 <= limit <= 100000 or type(window_seconds) is not int
                or not 1 <= window_seconds <= 86400):
            raise ValueError("模型限流配置无效")
        self.client = client
        # scope 是服务端配置的供应商账户别名，不是 API Key 或用户可控参数。
        self.key = "opspilot:model-quota:v1:" + hashlib.sha256(scope.encode()).hexdigest()
        self.limit = limit
        self.window_ms = window_seconds * 1000

    def acquire(self):
        try:
            admitted, ttl = self.client.eval(
                ACQUIRE_SCRIPT, 1, self.key, self.limit, self.window_ms,
            )
        except RedisError:
            # 不能退回单进程计数，否则失去所有实例共用配额的保证。
            raise ModelAdmissionUnavailable() from None
        if not admitted:
            raise ModelRateLimitExceeded(math.ceil(ttl / 1000))


@lru_cache(maxsize=8)
def _configured_limiter(url: str, scope: str, limit: int, window: int):
    # EVAL 超时可能已经计数，禁止底层透明重试；宁可保守占用名额。
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1,
                                 retry=Retry(NoBackoff(), 0))
    return RedisModelRateLimiter(client, scope=scope, limit=limit, window_seconds=window)


def acquire_model_request():
    """离线默认关闭；部署时显式配置。每次 HTTP 尝试都单独消耗名额。"""
    try:
        limit = int(os.getenv("MODEL_RATE_LIMIT_REQUESTS", "0"))
        if limit == 0:
            return
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            raise ValueError("缺少 Redis")
        limiter = _configured_limiter(
            url, os.getenv("MODEL_RATE_LIMIT_SCOPE", "zhipu-demo-account"), limit,
            int(os.getenv("MODEL_RATE_LIMIT_WINDOW_SECONDS", "60")),
        )
    except (ValueError, TypeError):
        raise ModelAdmissionUnavailable() from None
    limiter.acquire()
