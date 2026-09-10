"""用模拟 HTTP 响应验证完整连接；模型请求不出网，查询使用本地数据。"""

import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from agent_roundtrip import run_roundtrip
from grounding_policy import NO_EVIDENCE_ANSWER
from preview_tool_call import API_URL, build_initial_messages


def tool_reply(arguments='{"ticket_id":"T-1003"}', name="query_ticket"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "fixture_call_001",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
    }


class RoundtripTests(unittest.TestCase):
    def make_client(self, replies):
        requests = []

        def handler(request):
            self.assertEqual(str(request.url), API_URL)
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "glm-4.7-flash")
            requests.append(payload)
            index = len(requests) - 1
            self.assertLess(index, len(replies), "不允许超出预期请求次数")
            reply = replies[index]
            if isinstance(reply, int):
                return httpx.Response(reply, json={"error": {"code": "1305"}})
            return httpx.Response(200, json={"choices": [{"message": reply}]})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client, requests

    def test_success_preserves_call_and_sends_real_result(self):
        first = tool_reply()
        client, requests = self.make_client([
            first, {"role": "assistant", "content": "T-1003 的状态为 open，优先级为 medium。"}
        ])
        trace = run_roundtrip("fake-test-key", client)
        self.assertEqual(len(requests), 2)
        self.assertEqual(trace["tool_result"], {"id": "T-1003", "status": "open", "priority": "medium"})
        self.assertEqual(trace["model_requests"], 2)
        self.assertNotIn("tools", requests[1])
        self.assertNotIn("tool_choice", requests[1])
        history = requests[1]["messages"]
        self.assertEqual([item["role"] for item in history], ["system", "user", "assistant", "tool"])
        self.assertEqual(history[2], first)
        self.assertEqual(history[3]["tool_call_id"], first["tool_calls"][0]["id"])
        self.assertEqual(json.loads(history[3]["content"]), trace["tool_result"])
        self.assertEqual(len(build_initial_messages()), 2)

    def test_missing_ticket_is_sent_as_null(self):
        client, requests = self.make_client([
            tool_reply('{"ticket_id":"T-9999"}'),
            {"role": "assistant", "content": "未找到该工单。"},
        ])
        trace = run_roundtrip("fake-test-key", client)
        self.assertIsNone(trace["tool_result"])
        self.assertEqual(requests[1]["messages"][-1]["content"], "null")

    def test_empty_knowledge_evidence_stops_before_answer_model_request(self):
        first = tool_reply(
            '{"query":"食堂菜单","limit":3}',
            name="search_knowledge_base",
        )
        client, requests = self.make_client([first])

        with patch("agent_roundtrip.execute_tool", return_value=[]):
            trace = run_roundtrip("fake-test-key", client, question="食堂今天吃什么？")

        self.assertEqual(trace["answer"], NO_EVIDENCE_ANSWER)
        self.assertEqual(trace["tool_result"], [])
        self.assertEqual(trace["model_requests"], 1)
        self.assertEqual(len(requests), 1)

    def test_knowledge_answer_gets_verified_citation_from_tool_result(self):
        first = tool_reply(
            '{"query":"退款多久到账","limit":1}',
            name="search_knowledge_base",
        )
        evidence = [{
            "chunk_id": "refund-p2-c0",
            "title": "售后制度",
            "page": 2,
            "content": "退款在三个工作日内到账。",
            "score": 0.91,
        }]
        client, requests = self.make_client([
            first,
            {"role": "assistant", "content": "退款在三个工作日内到账。"},
        ])

        with patch("agent_roundtrip.execute_tool", return_value=evidence):
            trace = run_roundtrip("fake-test-key", client, question="退款多久到账？")

        self.assertEqual(
            trace["answer"],
            "退款在三个工作日内到账。\n\n来源：《售后制度》第2页",
        )
        self.assertEqual(len(requests), 2)

    def test_invalid_arguments_stop_before_query_and_second_request(self):
        client, requests = self.make_client([tool_reply('{"ticket_id":null}')])
        with patch("tool_executor.query_ticket") as query:
            with self.assertRaises(ValidationError):
                run_roundtrip("fake-test-key", client)
        query.assert_not_called()
        self.assertEqual(len(requests), 1)

    def test_unknown_tool_is_never_executed(self):
        client, requests = self.make_client([tool_reply(name="delete_ticket")])
        with patch("agent_roundtrip.execute_tool") as execute:
            with self.assertRaises(ValueError):
                run_roundtrip("fake-test-key", client)
        execute.assert_not_called()
        self.assertEqual(len(requests), 1)

    def test_missing_call_id_is_rejected_before_query(self):
        first = tool_reply()
        del first["tool_calls"][0]["id"]
        client, requests = self.make_client([first])
        with patch("agent_roundtrip.execute_tool") as execute:
            with self.assertRaises(ValueError):
                run_roundtrip("fake-test-key", client)
        execute.assert_not_called()
        self.assertEqual(len(requests), 1)

    def test_missing_tool_call_does_not_execute_query(self):
        client, requests = self.make_client([{"role": "assistant", "content": "请提供编号。"}])
        with patch("agent_roundtrip.execute_tool") as execute:
            with self.assertRaises(ValueError):
                run_roundtrip("fake-test-key", client)
        execute.assert_not_called()
        self.assertEqual(len(requests), 1)

    def test_additional_tool_call_does_not_start_another_step(self):
        client, requests = self.make_client([tool_reply(), tool_reply()])
        with self.assertRaises(ValueError):
            run_roundtrip("fake-test-key", client)
        self.assertEqual(len(requests), 2)

    def test_empty_final_answer_is_not_success(self):
        client, requests = self.make_client([tool_reply(), {"role": "assistant", "content": " "}])
        with self.assertRaises(ValueError):
            run_roundtrip("fake-test-key", client)
        self.assertEqual(len(requests), 2)

    def test_second_request_failure_is_not_retried(self):
        client, requests = self.make_client([tool_reply(), 429])
        with self.assertRaises(ValueError):
            run_roundtrip("fake-test-key", client)
        self.assertEqual(len(requests), 2)

    def test_user_question_reaches_both_model_requests(self):
        question = "查一下 T-1002 是否关闭"
        client, requests = self.make_client([
            tool_reply('{"ticket_id":"T-1002"}'),
            {"role": "assistant", "content": "工单已关闭。"},
        ])
        trace = run_roundtrip("fake-test-key", client, question=question)
        self.assertEqual(trace["question"], question)
        for request in requests:
            self.assertEqual(request["messages"][1]["content"], question)
        self.assertEqual(trace["tool_result"]["id"], "T-1002")

    def test_order_query_uses_dispatcher_and_returns_result_to_model(self):
        first = tool_reply('{"order_id":"O-2003"}', name="query_order")
        client, requests = self.make_client([
            first, {"role": "assistant", "content": "显示器订单已取消。"},
        ])
        with patch("tool_executor.query_ticket") as ticket_query:
            trace = run_roundtrip("fake-test-key", client, question="订单 O-2003 怎么样了？")
        ticket_query.assert_not_called()
        self.assertEqual(trace["tool_name"], "query_order")
        self.assertEqual(trace["tool_result"], {
            "id": "O-2003", "status": "cancelled", "product": "显示器",
            "delivered_at": None, "amount_cents": 159900,
        })
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            {tool["function"]["name"] for tool in requests[0]["tools"]},
            {"query_ticket", "query_order", "check_return_eligibility", "search_knowledge_base",
             "request_priority_change"},
        )
        self.assertNotIn("tools", requests[1])
        history = requests[1]["messages"]
        self.assertEqual(history[2], first)
        self.assertEqual(history[3]["tool_call_id"], "fixture_call_001")
        self.assertEqual(json.loads(history[3]["content"]), trace["tool_result"])

    def test_swapped_parameter_stops_before_either_query_or_second_request(self):
        for name, arguments in [
            ("query_order", '{"ticket_id":"T-1003"}'),
            ("query_ticket", '{"order_id":"O-2003"}'),
        ]:
            with self.subTest(tool=name):
                client, requests = self.make_client([tool_reply(arguments, name=name)])
                with patch("tool_executor.query_ticket") as ticket_query, \
                     patch("tool_executor.query_order") as order_query:
                    with self.assertRaises(ValidationError):
                        run_roundtrip("fake-test-key", client)
                ticket_query.assert_not_called()
                order_query.assert_not_called()
                self.assertEqual(len(requests), 1)

    def test_missing_order_is_returned_as_null(self):
        client, requests = self.make_client([
            tool_reply('{"order_id":"O-9999"}', name="query_order"),
            {"role": "assistant", "content": "没有找到该订单。"},
        ])
        trace = run_roundtrip("fake-test-key", client, question="查 O-9999")
        self.assertEqual(trace["tool_name"], "query_order")
        self.assertIsNone(trace["tool_result"])
        self.assertEqual(requests[1]["messages"][-1]["content"], "null")

    def test_two_different_tool_calls_are_rejected_before_dispatch(self):
        first = tool_reply()
        first["tool_calls"].append(tool_reply('{"order_id":"O-2003"}', name="query_order")["tool_calls"][0])
        client, requests = self.make_client([first])
        with patch("agent_roundtrip.execute_tool") as execute:
            with self.assertRaises(ValueError):
                run_roundtrip("fake-test-key", client)
        execute.assert_not_called()
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
