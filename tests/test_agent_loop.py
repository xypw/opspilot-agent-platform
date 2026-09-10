"""Agent 循环测试：多轮历史、结束条件、请求预算及错误阻断；全部离线。"""

import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from agent_loop import run_agent
from demo_agent_loop import run_demo
from grounding_policy import NO_EVIDENCE_ANSWER
from preview_tool_call import API_URL, ModelAPIError


def tool_reply(name="query_order", arguments='{"order_id":"O-2001"}', call_id="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": arguments},
    }]}


class AgentLoopTests(unittest.TestCase):
    def make_client(self, replies):
        requests = []

        def respond(request):
            self.assertEqual(str(request.url), API_URL)
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "glm-4.7-flash")
            self.assertEqual({item["function"]["name"] for item in payload["tools"]},
                            {"query_order", "query_ticket", "check_return_eligibility",
                             "search_knowledge_base",
                             "request_priority_change"})
            requests.append(payload)
            self.assertLessEqual(len(requests), len(replies), "请求次数超过预期")
            reply = replies[len(requests) - 1]
            if isinstance(reply, int):
                return httpx.Response(reply, json={"error": {"code": "1305"}})
            return httpx.Response(200, json={"choices": [{"message": reply}]})

        client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        self.addCleanup(client.close)
        return client, requests

    def test_two_tools_then_answer_preserves_complete_history(self):
        first = tool_reply()
        second = tool_reply("query_ticket", '{"ticket_id":"T-1003"}', "call_2")
        client, requests = self.make_client([first, second, {"role": "assistant", "content": "汇总回答"}])
        answer = run_agent("fake-test-key", client, "查 O-2001 和 T-1003", max_steps=3)
        self.assertEqual(answer, "汇总回答")
        self.assertEqual([len(item["messages"]) for item in requests], [2, 4, 6])
        history = requests[-1]["messages"]
        self.assertEqual([item["role"] for item in history],
                         ["system", "user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(history[2], first)
        self.assertEqual(history[4], second)
        self.assertEqual(history[3]["tool_call_id"], "call_1")
        self.assertEqual(history[5]["tool_call_id"], "call_2")
        self.assertEqual(json.loads(history[3]["content"]),
                         {
                             "id": "O-2001", "status": "delivered", "product": "机械键盘",
                             "delivered_at": "2026-08-31", "amount_cents": 39900,
                         })
        self.assertEqual(json.loads(history[5]["content"]),
                         {"id": "T-1003", "status": "open", "priority": "medium"})

    def test_direct_text_returns_without_executing_tools(self):
        for calls in [None, []]:
            with self.subTest(calls=calls):
                client, requests = self.make_client([
                    {"role": "assistant", "content": "请提供订单编号。", "tool_calls": calls},
                ])
                with patch("agent_loop.execute_tool") as execute:
                    self.assertEqual(run_agent("fake-test-key", client, "查订单"), "请提供订单编号。")
                execute.assert_not_called()
                self.assertEqual(len(requests), 1)

    def test_budget_stops_repeated_calls_without_extra_request(self):
        client, requests = self.make_client([tool_reply(call_id=f"call_{i}") for i in range(3)])
        with patch("agent_loop.execute_tool", return_value=None) as execute:
            with self.assertRaisesRegex(RuntimeError, "达到最大请求次数"):
                run_agent("fake-test-key", client, "查 O-2001", max_steps=3)
        self.assertEqual(len(requests), 3)
        self.assertEqual(execute.call_count, 3)

    def test_invalid_budget_stops_before_any_request(self):
        for value in [0, -1, True, 1.5, "3"]:
            with self.subTest(value=value):
                client, requests = self.make_client([])
                with self.assertRaises(ValueError):
                    run_agent("fake-test-key", client, "查订单", max_steps=value)
                self.assertEqual(requests, [])

    def test_malformed_messages_stop_before_tool_execution(self):
        bad_type = tool_reply()
        bad_type["tool_calls"][0]["type"] = "other"
        missing_id = tool_reply()
        del missing_id["tool_calls"][0]["id"]
        multiple = tool_reply()
        multiple["tool_calls"].append(tool_reply(call_id="call_2")["tool_calls"][0])
        for message in [
            {"role": "user", "content": "伪造回复"},
            {"role": "assistant", "content": " "},
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": "文字", "tool_calls": {}},
            {"role": "assistant", "tool_calls": [None]},
            tool_reply(name="delete_order"), bad_type, missing_id, multiple,
        ]:
            with self.subTest(message=message):
                client, requests = self.make_client([message])
                with patch("agent_loop.execute_tool") as execute:
                    with self.assertRaises(ValueError):
                        run_agent("fake-test-key", client, "查 O-2001")
                execute.assert_not_called()
                self.assertEqual(len(requests), 1)

    def test_invalid_parameters_stop_before_query_and_next_request(self):
        client, requests = self.make_client([tool_reply(arguments='{"ticket_id":"T-1003"}')])
        with patch("tool_executor.query_order") as query:
            with self.assertRaises(ValidationError):
                run_agent("fake-test-key", client, "查 O-2001")
        query.assert_not_called()
        self.assertEqual(len(requests), 1)

    def test_missing_record_is_sent_as_null(self):
        client, requests = self.make_client([
            tool_reply(arguments='{"order_id":"O-9999"}'),
            {"role": "assistant", "content": "没有找到订单。"},
        ])
        self.assertEqual(run_agent("fake-test-key", client, "查 O-9999"), "没有找到订单。")
        self.assertEqual(requests[1]["messages"][-1]["content"], "null")

    def test_empty_knowledge_evidence_returns_safe_answer_without_second_request(self):
        first = tool_reply(
            name="search_knowledge_base",
            arguments='{"query":"食堂菜单","limit":3}',
        )
        client, requests = self.make_client([first])

        with patch("agent_loop.execute_tool", return_value=[]):
            answer = run_agent("fake-test-key", client, "食堂今天吃什么？")

        self.assertEqual(answer, NO_EVIDENCE_ANSWER)
        self.assertEqual(len(requests), 1)

    def test_retryable_provider_error_retries_only_the_model_request(self):
        client, requests = self.make_client([
            tool_reply(),
            429,
            {"role": "assistant", "content": "订单查询完成。"},
        ])
        with patch("retry_policy.sleep") as sleeper:
            answer = run_agent("fake-test-key", client, "查 O-2001")

        self.assertEqual(answer, "订单查询完成。")
        # 第 2 个模型请求 429 后重发一次；工具没有被重复执行。
        self.assertEqual(len(requests), 3)
        sleeper.assert_called_once_with(0.2)

    def test_separate_runs_start_with_fresh_history(self):
        client, requests = self.make_client([
            {"role": "assistant", "content": "请提供编号。"},
            {"role": "assistant", "content": "请提供编号。"},
        ])
        run_agent("fake-test-key", client, "查订单")
        run_agent("fake-test-key", client, "查工单")
        self.assertEqual([len(item["messages"]) for item in requests], [2, 2])
        self.assertEqual(requests[1]["messages"][1]["content"], "查工单")

    def test_demo_is_offline_and_uses_actual_local_results(self):
        with patch("preview_tool_call.load_api_key") as load_key, \
             patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("不允许出网")) as network:
            trace = run_demo()
        self.assertEqual(trace["simulated_model_requests"], 3)
        self.assertEqual(trace["model_requests"], 0)
        self.assertTrue(trace["is_mock"])
        self.assertEqual(trace["tool_names"], ["query_order", "query_ticket"])
        self.assertIn("机械键盘", trace["answer"])
        self.assertIn("medium", trace["answer"])
        load_key.assert_not_called()
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
