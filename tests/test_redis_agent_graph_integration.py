"""真实 Redis 集成测试：新建图实例后仍能恢复等待确认的 Agent。"""

from copy import deepcopy
import os
import unittest
from uuid import uuid4

from agent_graph import (
    AgentGraphResumeRequest,
    AgentGraphStartRequest,
    ConfiguredAgentModelGateway,
    build_agent_graph,
    get_agent_graph_state,
    resume_agent_graph,
    start_agent_graph,
)
from langgraph_checkpointer import build_agent_checkpointer
from redis_checkpoint_store import RedisPendingActionStore, create_redis_client
from runtime_store_factory import load_redis_url
from tickets import TICKETS, query_ticket


@unittest.skipUnless(
    os.getenv("RUN_REDIS_INTEGRATION") == "1",
    "仅在 RUN_REDIS_INTEGRATION=1 且 Docker Redis 已启动时运行真实集成测试。",
)
class RedisAgentGraphIntegrationTests(unittest.TestCase):
    """不依赖任何共享 Python 对象，模拟 Web 服务重启后重新建图。"""

    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.redis_url = load_redis_url()
        if not self.redis_url:
            self.skipTest("未配置 REDIS_URL，无法验证 Redis 恢复。")
        # 每次运行使用独立命名空间，不碰开发环境已有的待确认操作。
        self.namespace = f"opspilot_test_restart_{uuid4().hex}"
        self.thread_id = f"redis-restart-{uuid4().hex}"

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def test_new_graph_instance_resumes_waiting_priority_change(self):
        # 第一个“进程”创建动作并在 interrupt 处暂停。
        first_store = RedisPendingActionStore(
            create_redis_client(self.redis_url), namespace=self.namespace
        )
        first_graph = build_agent_graph(
            first_store,
            ConfiguredAgentModelGateway(),
            checkpointer=build_agent_checkpointer(self.redis_url),
        )
        waiting = start_agent_graph(first_graph, AgentGraphStartRequest(
            thread_id=self.thread_id,
            message="把工单 T-1003 的优先级改成 high",
            mode="mock",
        ))
        self.assertEqual(waiting.status, "WAITING_CONFIRMATION")
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")

        # 第二个“进程”重新创建 Redis 客户端、Checkpointer、ActionStore 和图。
        second_store = RedisPendingActionStore(
            create_redis_client(self.redis_url), namespace=self.namespace
        )
        second_graph = build_agent_graph(
            second_store,
            ConfiguredAgentModelGateway(),
            checkpointer=build_agent_checkpointer(self.redis_url),
        )
        restored = get_agent_graph_state(second_graph, self.thread_id)
        self.assertEqual(restored.status, "WAITING_CONFIRMATION")
        self.assertEqual(restored.action_id, waiting.action_id)

        # 恢复时只传用户确认；LangGraph 从 Redis 读取暂停点之后的 State。
        completed = resume_agent_graph(
            second_graph,
            self.thread_id,
            AgentGraphResumeRequest(approved=True),
        )
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(query_ticket("T-1003")["priority"], "high")


if __name__ == "__main__":
    unittest.main()
