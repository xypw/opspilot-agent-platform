"""为 OpsPilot Agent 创建 LangGraph Checkpointer。"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.redis import RedisSaver

from runtime_store_factory import load_redis_url


class CheckpointerConfigurationError(RuntimeError):
    """显式配置 Redis 后无法初始化 Checkpointer，应用不应悄悄退回内存版。"""


def build_agent_checkpointer(redis_url: str | None = None):
    """优先使用 Redis；未配置地址时保留离线教学用 InMemorySaver。"""
    url = load_redis_url() if redis_url is None else redis_url.strip()
    if not url:
        return InMemorySaver()

    try:
        saver = RedisSaver(url)
        # setup 是幂等的：首次创建索引，后续服务重启时确认索引仍存在。
        saver.setup()
    except Exception as error:
        # 不回显 URL，因为生产 URL 可能带有密码；保留原异常供服务器日志定位。
        raise CheckpointerConfigurationError("Redis Checkpointer 初始化失败。") from error
    return saver
