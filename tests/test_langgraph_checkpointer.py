"""LangGraph Checkpointer 工厂测试：未配置时内存，配置后初始化 RedisSaver。"""

import unittest
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from langgraph_checkpointer import (
    CheckpointerConfigurationError,
    build_agent_checkpointer,
)


class LangGraphCheckpointerTests(unittest.TestCase):
    def test_missing_url_uses_in_memory_saver(self):
        saver = build_agent_checkpointer("")
        self.assertIsInstance(saver, InMemorySaver)

    def test_configured_url_creates_and_sets_up_redis_saver(self):
        # Mock 能记录 setup 调用；普通 object 不能临时增加方法。
        fake_saver = Mock()
        with patch("langgraph_checkpointer.RedisSaver", return_value=fake_saver) as saver_class:
            result = build_agent_checkpointer("redis://example:6379/0")
        self.assertIs(result, fake_saver)
        saver_class.assert_called_once_with("redis://example:6379/0")
        fake_saver.setup.assert_called_once_with()

    def test_redis_failure_does_not_silently_fall_back_to_memory(self):
        with patch("langgraph_checkpointer.RedisSaver", side_effect=OSError("connection failed")):
            with self.assertRaises(CheckpointerConfigurationError):
                build_agent_checkpointer("redis://example:6379/0")


if __name__ == "__main__":
    unittest.main()
