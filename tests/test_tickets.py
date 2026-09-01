"""学习者查询函数的验收测试：首条、末条和不存在的工单。"""

import unittest

from tickets import query_ticket


class QueryTicketTests(unittest.TestCase):
    def test_find_first_ticket(self):
        self.assertEqual(
            query_ticket("T-1001"),
            {"id": "T-1001", "status": "open", "priority": "high"},
        )

    def test_find_last_ticket(self):
        self.assertEqual(
            query_ticket("T-1003"),
            {"id": "T-1003", "status": "open", "priority": "medium"},
        )

    def test_missing_ticket_returns_none(self):
        self.assertIsNone(query_ticket("T-9999"))


if __name__ == "__main__":
    unittest.main()
