"""模型请求的受控重试策略。

重试只用于“请求还没有得到有效模型响应”的阶段。工具执行、人工确认和任何写操作
都不在本模块的重试范围内，避免网络抖动导致业务副作用重复发生。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter, sleep

import httpx

from preview_tool_call import ModelAPIError, request_message


# 408/429/5xx 通常表示暂时性服务或网络问题；400、401、403 等配置/请求错误不重试。
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.2


@dataclass
class ModelRequestTelemetry:
    """一次模型轮次内部的 HTTP 尝试、重试和耗时。"""

    http_attempts: int = 0
    retry_count: int = 0
    duration_ms: float = 0.0
    attempt_durations_ms: list[float] = field(default_factory=list)


def is_retryable_error(error: Exception) -> bool:
    """只识别可恢复的传输错误和有限集合的上游状态码。"""
    if isinstance(error, httpx.RequestError):
        return True
    if isinstance(error, ModelAPIError):
        return error.status_code in RETRYABLE_STATUS_CODES
    return False


def request_message_with_retry(
    api_key: str,
    client: httpx.Client,
    messages: list[dict],
    *,
    offer_tools: bool = True,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleeper: Callable[[float], None] | None = None,
    telemetry: ModelRequestTelemetry | None = None,
) -> dict:
    """获取一条模型消息；仅在安全的暂时性错误上做有限重试。

    指数退避为 0.2 秒、0.4 秒……避免大量请求在服务繁忙时同时再次冲击上游。
    测试通过注入 sleeper 避免真实等待，因此测试既快又能验证退避次数。
    """
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    delay = sleeper or sleep
    # 每次模型轮次使用一个新对象；调用方传入时可以在成功或异常后读取数据。
    metrics = telemetry or ModelRequestTelemetry()
    started_at = perf_counter()

    try:
        for attempt in range(max_attempts):
            attempt_started_at = perf_counter()
            try:
                return request_message(api_key, client, messages, offer_tools=offer_tools)
            except (httpx.RequestError, ModelAPIError) as error:
                # 最后一次失败或不可恢复错误必须原样抛出，让 API 层返回明确状态。
                if attempt == max_attempts - 1 or not is_retryable_error(error):
                    raise
                # 只有真正进入下一次 HTTP 请求，才把本次失败记为一次重试。
                metrics.retry_count += 1
                delay(BASE_DELAY_SECONDS * (2 ** attempt))
            finally:
                # 无论成功、失败还是响应格式错误，每次发出的 HTTP 请求都必须计数。
                metrics.http_attempts += 1
                metrics.attempt_durations_ms.append(
                    round((perf_counter() - attempt_started_at) * 1000, 3)
                )
    finally:
        # 模型轮次总耗时包含 HTTP、解析和指数退避等待时间。
        metrics.duration_ms = round((perf_counter() - started_at) * 1000, 3)

    # for 循环必定在 return 或 raise 处结束，这行只为静态分析器提供完整返回路径。
    raise RuntimeError("模型重试循环意外结束")
