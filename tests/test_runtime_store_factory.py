"""运行时存储选择测试：不配置 Redis 时保持离线内存模式。"""

import unittest
from unittest.mock import patch

from checkpoint_store import AgentRunStore
from pending_actions import PendingActionStore
from redis_checkpoint_store import RedisAgentRunStore, RedisPendingActionStore
from runtime_store_factory import build_runtime_stores, build_ticket_repository
from ticket_repository import ConfiguredPostgresTicketRepository
from tickets import InMemoryTicketRepository


class FakeRedis:
    def ping(self):
        return True


class RuntimeStoreFactoryTests(unittest.TestCase):
    def test_empty_url_uses_existing_in_memory_stores(self):
        stores = build_runtime_stores("")

        self.assertFalse(stores.redis_enabled)
        self.assertIsInstance(stores.action_store, PendingActionStore)
        self.assertIsInstance(stores.run_store, AgentRunStore)
        self.assertIsInstance(stores.ticket_repository, InMemoryTicketRepository)

    def test_configured_url_creates_two_redis_stores_sharing_one_action_store(self):
        with patch("runtime_store_factory.create_redis_client", return_value=FakeRedis()):
            stores = build_runtime_stores("redis://example.test:6379/0")

        self.assertTrue(stores.redis_enabled)
        self.assertIsInstance(stores.action_store, RedisPendingActionStore)
        self.assertIsInstance(stores.run_store, RedisAgentRunStore)
        self.assertIs(stores.run_store._action_store, stores.action_store)

    def test_ticket_repository_is_injected_into_action_store(self):
        repository = InMemoryTicketRepository()

        stores = build_runtime_stores("", ticket_repository=repository)

        self.assertIs(stores.action_store._ticket_repository, repository)
        self.assertIs(stores.ticket_repository, repository)

    def test_backend_name_selects_repository_implementation(self):
        self.assertIsInstance(build_ticket_repository("memory"), InMemoryTicketRepository)
        self.assertIsInstance(
            build_ticket_repository(" POSTGRES "),
            ConfiguredPostgresTicketRepository,
        )

    def test_unknown_backend_fails_instead_of_silently_using_memory(self):
        with self.assertRaisesRegex(ValueError, "memory 或 postgres"):
            build_ticket_repository("postgers")


if __name__ == "__main__":
    unittest.main()
