"""PostgreSQL 工单仓库单元测试：使用假连接验证 SQL 契约。"""

import unittest

from ticket_repository import PostgresTicketRepository


class FakeCursor:
    """记录 execute 参数的最小 Cursor 替身，不需要真实 PostgreSQL。"""

    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None
        self.row_factory = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self, *, row_factory):
        self.fake_cursor.row_factory = row_factory
        return self.fake_cursor


class PostgresTicketRepositoryTests(unittest.TestCase):
    def test_get_by_id_uses_parameterized_select(self):
        cursor = FakeCursor({"id": "T-1003", "status": "open", "priority": "medium"})
        repository = PostgresTicketRepository(lambda: FakeConnection(cursor))

        ticket = repository.get_by_id(" T-1003 ")

        self.assertEqual(ticket, {"id": "T-1003", "status": "open", "priority": "medium"})
        self.assertIn("WHERE id = %s", cursor.sql)
        self.assertEqual(cursor.params, ("T-1003",))

    def test_change_priority_uses_returning_and_two_parameters(self):
        cursor = FakeCursor({"id": "T-1003", "status": "open", "priority": "high"})
        repository = PostgresTicketRepository(lambda: FakeConnection(cursor))

        ticket = repository.change_priority("T-1003", "high")

        self.assertEqual(ticket["priority"], "high")
        self.assertIn("UPDATE tickets", cursor.sql)
        self.assertIn("RETURNING id, status, priority", cursor.sql)
        self.assertEqual(cursor.params, ("high", "T-1003"))

    def test_invalid_parameters_fail_before_sql_runs(self):
        repository = PostgresTicketRepository(lambda: self.fail("不应创建数据库连接"))

        with self.assertRaisesRegex(ValueError, "ticket_id"):
            repository.get_by_id("   ")
        with self.assertRaisesRegex(ValueError, "new_priority"):
            repository.change_priority("T-1003", "urgent")


if __name__ == "__main__":
    unittest.main()
