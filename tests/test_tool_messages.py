"""工具消息构造验收：角色、调用 ID 和结果的 JSON 序列化。"""

import json
import unittest

from tool_messages import build_tool_message


class ToolMessageTests(unittest.TestCase):
    def test_role_and_id_match_the_call(self):
        message = build_tool_message("call_demo_002", {"id": "T-1003"})
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call_demo_002")
        self.assertEqual(set(message), {"role", "tool_call_id", "content"})

    def test_content_is_json_text_preserving_result(self):
        result = {"id": "T-1003", "status": "open", "priority": "medium"}
        message = build_tool_message("call_demo_003", result)
        self.assertIsInstance(message["content"], str)
        self.assertEqual(json.loads(message["content"]), result)
        self.assertEqual(result, {"id": "T-1003", "status": "open", "priority": "medium"})

    def test_missing_ticket_serializes_as_json_null(self):
        message = build_tool_message("call_demo_004", None)
        self.assertEqual(message["content"], "null")

    def test_chinese_text_remains_readable(self):
        result = {"description": "无法登录"}
        message = build_tool_message("call_demo_005", result)
        self.assertIn("无法登录", message["content"])
        self.assertEqual(json.loads(message["content"]), result)


if __name__ == "__main__":
    unittest.main()
