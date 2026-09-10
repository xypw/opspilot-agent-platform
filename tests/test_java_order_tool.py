"""订单工具适配层测试：参数校验后才允许调用 Java 客户端。"""

import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from java_order_tool import query_order_via_java


class JavaOrderToolTests(unittest.TestCase):
    def test_validates_and_passes_normalized_id_to_java(self):
        client = Mock()
        client.get_by_id.return_value = {
            "id": "O-2003",
            "status": "cancelled",
            "product": "显示器",
            "delivered_at": None,
            "amount_cents": 159900,
        }

        result = query_order_via_java('{"order_id":"  O-2003  "}', client)

        client.get_by_id.assert_called_once_with("O-2003")
        self.assertEqual(result["id"], "O-2003")

    def test_invalid_arguments_do_not_call_java(self):
        client = Mock()

        with self.assertRaises(ValidationError):
            query_order_via_java('{"ticket_id":"T-1001"}', client)

        client.get_by_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()
