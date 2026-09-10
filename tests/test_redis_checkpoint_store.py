"""Redis Checkpoint 仓库测试：FakeRedis 模拟跨 store 实例的数据持久化。"""

from copy import deepcopy
import unittest

from checkpoint_store import RunStateError
from pending_actions import ActionStateError
from redis_checkpoint_store import RedisAgentRunStore, RedisPendingActionStore
from tickets import TICKETS, query_ticket


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def set(self, name, value):
        self.commands.append((name, value))
        return self

    def execute(self):
        for name, value in self.commands:
            self.client.set(name, value)
        return [True] * len(self.commands)


class FakeRedis:
    """只实现本模块使用的最小行为；真实 Redis 的网络与持久化由集成环境负责。"""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value, nx=False):
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def lock(self, name, timeout, blocking_timeout):
        return FakeLock()


def pending_chat_result(action_id):
    return {
        "tool_name": "request_priority_change",
        "tool_result": {"action_id": action_id, "status": "pending"},
        "answer": "等待用户确认。",
        "model_requests": 0,
        "simulated_model_requests": 2,
    }


class RedisCheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.original_tickets = deepcopy(TICKETS)
        self.redis = FakeRedis()
        self.actions = RedisPendingActionStore(
            self.redis, id_factory=lambda: "act_redis_001"
        )
        self.runs = RedisAgentRunStore(
            self.redis, self.actions, id_factory=lambda: "run_redis_001"
        )

    def tearDown(self):
        TICKETS[:] = self.original_tickets

    def test_second_store_instance_can_read_persisted_pending_action(self):
        action = self.actions.propose_priority_change("T-1003", "high")
        restarted_store = RedisPendingActionStore(self.redis)

        self.assertEqual(restarted_store.get(action.action_id), action)

    def test_idempotent_start_survives_a_new_store_instance(self):
        action = self.actions.propose_priority_change("T-1003", "high")
        first = self.runs.start(
            "thread-1", "request-1", "修改 T-1003",
            lambda message: pending_chat_result(action.action_id),
        )
        restarted_runs = RedisAgentRunStore(self.redis, self.actions)
        second = restarted_runs.start(
            "thread-1", "request-1", "修改 T-1003",
            lambda message: self.fail("幂等重试不应重新运行 Agent"),
        )

        self.assertEqual(second, first)
        self.assertEqual(restarted_runs.get(first.run_id), first)

    def test_cancelled_redis_run_cannot_be_confirmed_after_restart(self):
        action = self.actions.propose_priority_change("T-1003", "high")
        run = self.runs.start(
            "thread-1", "cancel-1", "修改 T-1003",
            lambda message: pending_chat_result(action.action_id),
        )
        cancelled = self.runs.cancel(run.run_id)
        restarted_runs = RedisAgentRunStore(self.redis, self.actions)

        self.assertEqual(cancelled.status, "CANCELLED")
        with self.assertRaises(RunStateError):
            restarted_runs.confirm(run.run_id)
        with self.assertRaises(ActionStateError):
            self.actions.confirm(action.action_id)
        self.assertEqual(query_ticket("T-1003")["priority"], "medium")


if __name__ == "__main__":
    unittest.main()
