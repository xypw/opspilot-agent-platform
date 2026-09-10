"""订单查询验收：首条、末条、不存在的编号以及不修改数据。"""

from copy import deepcopy
import unittest

from orders import ORDERS, query_order


class OrderQueryTests(unittest.TestCase):
    def setUp(self):
        self.original_orders = deepcopy(ORDERS)

    def tearDown(self):
        try:
            self.assertEqual(ORDERS, self.original_orders, "查询不应修改订单数据")
        finally:
            # 即使学习者误修改数据，也不让某个测试影响下一个测试。
            ORDERS[:] = self.original_orders

    def test_returns_first_order(self):
        self.assertEqual(
            query_order("O-2001"),
            {
                "id": "O-2001", "status": "delivered", "product": "机械键盘",
                "delivered_at": "2026-08-31", "amount_cents": 39900,
            },
        )

    def test_returns_last_order(self):
        self.assertEqual(
            query_order("O-2003"),
            {
                "id": "O-2003", "status": "cancelled", "product": "显示器",
                "delivered_at": None, "amount_cents": 159900,
            },
        )

    def test_returns_none_when_not_found(self):
        self.assertIsNone(query_order("O-9999"))


if __name__ == "__main__":
    unittest.main()
