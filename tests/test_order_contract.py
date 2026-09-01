"""订单工具参数模型和工具说明的契约测试。"""

import unittest

from pydantic import ValidationError

from tool_args import QueryOrderArgs
from tool_schema import QUERY_ORDER_TOOL


class OrderContractTests(unittest.TestCase):
    def test_order_id_is_trimmed(self):
        args = QueryOrderArgs.model_validate({"order_id": " O-2002 "})
        self.assertEqual(args.order_id, "O-2002")

    def test_ticket_field_and_invalid_order_ids_are_rejected(self):
        for args in [{"ticket_id": "T-1001"}, {}, {"order_id": None},
                     {"order_id": "   "}, {"order_id": 123}]:
            with self.subTest(args=args):
                with self.assertRaises(ValidationError):
                    QueryOrderArgs.model_validate(args)

    def test_schema_matches_order_function_and_parameter(self):
        function = QUERY_ORDER_TOOL["function"]
        self.assertEqual(function["name"], "query_order")
        self.assertEqual(function["parameters"]["required"], ["order_id"])
        self.assertEqual(function["parameters"]["properties"]["order_id"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
