"""参数解析与查询连接测试；不调用模型，也不读取密钥。"""

import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from execute_tool_example import execute_query_example


class ExecuteQueryExampleTests(unittest.TestCase):
    def test_query_returns_whole_ticket(self):
        self.assertEqual(
            execute_query_example('{"ticket_id":"T-1003"}'),
            {"id": "T-1003", "status": "open", "priority": "medium"},
        )

    def test_missing_ticket_returns_none(self):
        self.assertIsNone(execute_query_example('{"ticket_id":"T-9999"}'))

    def test_passes_parsed_id_instead_of_hardcoded_value(self):
        with patch("execute_tool_example.query_ticket", return_value={"id": "T-1002"}) as query:
            result = execute_query_example('{"ticket_id":"T-1002"}')
        query.assert_called_once_with("T-1002")
        self.assertEqual(result, {"id": "T-1002"})

    def test_invalid_json_does_not_execute_query(self):
        with patch("execute_tool_example.query_ticket") as query:
            with self.assertRaises(json.JSONDecodeError):
                execute_query_example("not JSON")
        query.assert_not_called()

    def test_missing_field_is_rejected_before_query(self):
        with patch("execute_tool_example.query_ticket") as query:
            with self.assertRaises(ValidationError):
                execute_query_example("{}")
        query.assert_not_called()

    def test_query_receives_trimmed_ticket_id(self):
        with patch("execute_tool_example.query_ticket", return_value={"id": "T-1003"}) as query:
            result = execute_query_example('{"ticket_id":"  T-1003  "}')
        query.assert_called_once_with("T-1003")
        self.assertEqual(result, {"id": "T-1003"})

    def test_invalid_parameters_never_execute_query(self):
        for args in [None, [], "T-1003", {"ticket_id": None}, {"ticket_id": 123},
                     {"ticket_id": ""}, {"ticket_id": "   "}]:
            with self.subTest(args=args):
                with patch("execute_tool_example.query_ticket") as query:
                    with self.assertRaises(ValidationError):
                        execute_query_example(json.dumps(args))
                query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
