"""查询参数模型的独立测试；不调用工具或模型服务。"""

import unittest

from pydantic import ValidationError

from tool_args import QueryTicketArgs


class QueryTicketArgsTests(unittest.TestCase):
    def test_valid_ticket_id(self):
        args = QueryTicketArgs(ticket_id="T-1003")
        self.assertEqual(args.ticket_id, "T-1003")

    def test_strip_surrounding_whitespace(self):
        args = QueryTicketArgs(ticket_id=" \tT-1003\n ")
        self.assertEqual(args.ticket_id, "T-1003")

    def test_missing_ticket_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            QueryTicketArgs()

    def test_empty_and_wrong_type_values_are_rejected(self):
        for value in ["", "   ", "\t\n", None, 123, True, [], {}]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    QueryTicketArgs(ticket_id=value)


if __name__ == "__main__":
    unittest.main()
