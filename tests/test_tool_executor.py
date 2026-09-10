"""工具分发验收：正确路由、正确参数模型、失败时不执行查询。"""

import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

import tool_executor
from tool_executor import execute_tool


class ToolExecutorTests(unittest.TestCase):
    def test_return_eligibility_uses_injected_java_query_after_validation(self):
        result_from_java = {
            "order_id": "O-2001",
            "decision": "NO_REASON_ALLOWED",
            "can_apply": True,
            "reason_required": False,
            "days_since_delivery": 7,
            "reason": "WITHIN_7_DAY_NO_REASON_WINDOW",
        }
        injected_query = unittest.mock.Mock(return_value=result_from_java)

        with patch.object(tool_executor, "RETURN_ELIGIBILITY_QUERY", injected_query):
            result = execute_tool(
                "check_return_eligibility", '{"order_id":"  O-2001  "}'
            )

        injected_query.assert_called_once_with("O-2001")
        self.assertEqual(result, result_from_java)

    def test_order_branch_uses_injected_query_after_validation(self):
        injected_query = unittest.mock.Mock(
            return_value={
                "id": "O-2002", "status": "processing", "product": "无线鼠标",
                "delivered_at": None, "amount_cents": 12900,
            }
        )

        with patch.object(tool_executor, "ORDER_QUERY", injected_query):
            result = execute_tool("query_order", '{"order_id":"  O-2002  "}')

        injected_query.assert_called_once_with("O-2002")
        self.assertEqual(result["id"], "O-2002")

    def test_queries_return_real_local_records(self):
        self.assertEqual(
            execute_tool("query_ticket", '{"ticket_id":"T-1003"}'),
            {"id": "T-1003", "status": "open", "priority": "medium"},
        )
        self.assertEqual(
            execute_tool("query_order", '{"order_id":"O-2003"}'),
            {
                "id": "O-2003", "status": "cancelled", "product": "显示器",
                "delivered_at": None, "amount_cents": 159900,
            },
        )

    def test_missing_records_return_none(self):
        self.assertIsNone(execute_tool("query_ticket", '{"ticket_id":"T-9999"}'))
        self.assertIsNone(execute_tool("query_order", '{"order_id":"O-9999"}'))

    def test_knowledge_search_returns_citable_evidence(self):
        results = execute_tool(
            "search_knowledge_base", '{"query":"退款多久到账","limit":1}'
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "售后与退款制度")
        self.assertEqual(results[0]["page"], 2)

    def test_dispatch_calls_only_selected_query_with_normalized_id(self):
        for name, field, record_id in [
            ("query_ticket", "ticket_id", "T-1002"),
            ("query_order", "order_id", "O-2002"),
        ]:
            with self.subTest(tool=name):
                with patch.object(tool_executor.TICKET_REPOSITORY, "get_by_id") as ticket_query, \
                     patch("tool_executor.query_order") as order_query:
                    selected, other = (
                        (ticket_query, order_query) if name == "query_ticket"
                        else (order_query, ticket_query)
                    )
                    selected.return_value = {"id": record_id}
                    result = execute_tool(name, json.dumps({field: f"  {record_id}  "}))
                    selected.assert_called_once_with(record_id)
                    other.assert_not_called()
                    self.assertEqual(result, {"id": record_id})

    def test_swapped_field_names_are_rejected_before_either_query(self):
        for name, args in [
            ("query_order", {"ticket_id": "T-1003"}),
            ("query_ticket", {"order_id": "O-2003"}),
        ]:
            with self.subTest(tool=name):
                self.assert_no_query(name, json.dumps(args), ValidationError)

        with patch.object(tool_executor, "RETURN_ELIGIBILITY_QUERY") as eligibility_query:
            with self.assertRaises(ValidationError):
                execute_tool("check_return_eligibility", '{"ticket_id":"T-1003"}')
            eligibility_query.assert_not_called()

    def test_invalid_parameters_are_rejected_before_either_query(self):
        for name, field in [("query_ticket", "ticket_id"), ("query_order", "order_id")]:
            for args in [None, [], "not an object", {}, {field: None},
                         {field: 123}, {field: ""}, {field: "   "}]:
                with self.subTest(tool=name, args=args):
                    self.assert_no_query(name, json.dumps(args), ValidationError)

    def test_invalid_json_is_rejected_before_either_query(self):
        for name in ["query_ticket", "query_order"]:
            with self.subTest(tool=name):
                self.assert_no_query(name, "not JSON", json.JSONDecodeError)

    def test_unknown_tools_are_rejected_before_either_query(self):
        for name in ["delete_order", "", "Query_Order"]:
            with self.subTest(tool=name):
                self.assert_no_query(name, '{"order_id":"O-2003"}', ValueError)

    def assert_no_query(self, name, arguments_json, error_type):
        with patch.object(tool_executor.TICKET_REPOSITORY, "get_by_id") as ticket_query, \
             patch("tool_executor.query_order") as order_query:
            with self.assertRaises(error_type):
                execute_tool(name, arguments_json)
            ticket_query.assert_not_called()
            order_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
