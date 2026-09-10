"""真实 PostgreSQL 集成测试：显式开启后验证查询和更新。"""

import os
import unittest

from pending_actions import PendingActionStore
from ticket_repository import ConfiguredPostgresTicketRepository


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "仅在 RUN_POSTGRES_INTEGRATION=1 且 Docker PostgreSQL 已启动时运行。",
)
class PostgresTicketRepositoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repository = ConfiguredPostgresTicketRepository()
        self.original = self.repository.get_by_id("T-1003")

    def tearDown(self):
        # 恢复固定演示数据，避免测试影响后续演示。
        if self.original is not None:
            self.repository.change_priority("T-1003", self.original["priority"])

    def test_query_then_update_returns_persisted_ticket(self):
        self.assertEqual(self.original["priority"], "medium")

        updated = self.repository.change_priority("T-1003", "high")
        reread = self.repository.get_by_id("T-1003")

        self.assertEqual(updated["priority"], "high")
        self.assertEqual(reread, updated)

    def test_confirmed_agent_action_persists_to_postgres(self):
        store = PendingActionStore(
            id_factory=lambda: "act_postgres_001",
            ticket_repository=self.repository,
        )

        pending = store.propose_priority_change("T-1003", "high")
        before_confirmation = self.repository.get_by_id("T-1003")
        executed = store.confirm(pending.action_id)
        after_confirmation = self.repository.get_by_id("T-1003")

        self.assertEqual(pending.status, "pending")
        self.assertEqual(before_confirmation["priority"], "medium")
        self.assertEqual(executed.status, "executed")
        self.assertEqual(after_confirmation["priority"], "high")


if __name__ == "__main__":
    unittest.main()
