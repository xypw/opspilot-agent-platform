"""事实核对契约与 LangGraph 接入的离线测试。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from agent_graph import ConfiguredAgentModelGateway, AgentGraphStartRequest, build_agent_graph, start_agent_graph
from evidence_review import (
    build_evidence_review_messages,
    parse_evidence_review_message,
    verify_evidence_review,
)
from retrieval_evaluation import extract_context_chunk_ids
from pending_actions import PendingActionStore


CANDIDATES = [{
    "chunk_id": "refund-1", "title": "退款政策", "page": 1,
    "content": "退款审核通过后三个工作日到账。电子发票发送至邮箱。", "score": 0.888,
}]
SUPPORTED = {
    "supported": True,
    "supporting_quotes": [{"chunk_id": "refund-1", "text": "退款审核通过后三个工作日到账。"}],
    "missing_information": "",
}
UNSUPPORTED = {"supported": False, "supporting_quotes": [], "missing_information": "没有到账时间"}


class EvidenceReviewTests(unittest.TestCase):
    def test_builds_untrusted_candidate_prompt_and_parses_strict_json(self):
        messages = build_evidence_review_messages("退款多久到账", CANDIDATES)
        self.assertIn("不可信数据", messages[0]["content"])
        self.assertNotIn("电子发票", messages[0]["content"])
        self.assertIn("电子发票", messages[1]["content"])
        parsed = parse_evidence_review_message({
            "role": "assistant", "content": json.dumps(SUPPORTED),
        })
        self.assertEqual(parsed, SUPPORTED)

    def test_parses_provider_json_answer_wrapper(self):
        wrapped = {"answer": json.dumps(SUPPORTED, ensure_ascii=False)}
        parsed = parse_evidence_review_message({
            "role": "assistant", "content": json.dumps(wrapped, ensure_ascii=False),
        })
        self.assertEqual(parsed, SUPPORTED)

    def test_parses_object_wrapper_but_still_validates_inner_contract(self):
        parsed = parse_evidence_review_message({
            "role": "assistant", "content": json.dumps({"answer": SUPPORTED}),
        })
        verify_evidence_review(parsed, CANDIDATES)
        with self.assertRaises(ValueError):
            verify_evidence_review({"answer": SUPPORTED, "extra": True}, CANDIDATES)

    def test_candidate_limit_matches_maximum_tool_recall(self):
        candidates = [{**CANDIDATES[0], "chunk_id": f"chunk-{i}"} for i in range(15)]
        self.assertEqual(len(build_evidence_review_messages("退款多久到账", candidates)), 2)
        with self.assertRaises(ValueError):
            build_evidence_review_messages("退款多久到账", candidates + [{**CANDIDATES[0]}])

    def test_rejects_invalid_provider_json_answer_wrapper(self):
        with self.assertRaisesRegex(ValueError, "answer 字段不是严格 JSON"):
            parse_evidence_review_message({
                "role": "assistant", "content": json.dumps({"answer": "not-json"}),
            })

    def test_rejects_non_json_or_tool_call_review_response(self):
        for message in (
            {"role": "assistant", "content": "```json\n{}\n```"},
            {"role": "assistant", "content": "{}", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "content": "{}"},
        ):
            with self.subTest(message=message), self.assertRaises(ValueError):
                parse_evidence_review_message(message)

    def test_selects_only_verbatim_quotes_and_preserves_source_metadata(self):
        original = deepcopy(CANDIDATES)
        _, selected = verify_evidence_review(SUPPORTED, CANDIDATES)
        self.assertEqual(selected[0]["content"], "退款审核通过后三个工作日到账。")
        self.assertEqual(selected[0]["title"], "退款政策")
        self.assertEqual(CANDIDATES, original)

    def test_rejects_fabricated_text_or_unknown_chunk(self):
        for quote in (
            {"chunk_id": "refund-1", "text": "一小时到账"},
            {"chunk_id": "unknown", "text": "退款审核通过后三个工作日到账。"},
            {"chunk_id": "refund-1", "text": " "},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                verify_evidence_review({**SUPPORTED, "supporting_quotes": [quote]}, CANDIDATES)

    def test_rejects_inconsistent_and_non_boolean_verdicts(self):
        for verdict in (
            {**SUPPORTED, "supported": "true"},
            {**SUPPORTED, "supporting_quotes": []},
            {**SUPPORTED, "missing_information": "没有时间"},
            {**UNSUPPORTED, "supporting_quotes": SUPPORTED["supporting_quotes"]},
            {**UNSUPPORTED, "missing_information": " "},
        ):
            with self.subTest(verdict=verdict), self.assertRaises(ValueError):
                verify_evidence_review(verdict, CANDIDATES)

    def test_rejects_ambiguous_duplicate_chunk_ids(self):
        with self.assertRaises(ValueError):
            verify_evidence_review(SUPPORTED, CANDIDATES * 2)


class EvidenceReviewGraphTests(unittest.TestCase):
    def build_graph(self, reviewer, candidates=None):
        self.gateway = Mock(wraps=ConfiguredAgentModelGateway())
        self.candidates = deepcopy(CANDIDATES if candidates is None else candidates)
        return build_agent_graph(
            PendingActionStore(), self.gateway,
            tool_runner=lambda name, args: self.candidates,
            evidence_reviewer=reviewer,
            evidence_review_modes=frozenset({"mock"}),
        )

    def start(self, graph):
        return start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="review-test", message="退款多久到账", mode="mock",
        ))

    def test_unsupported_stops_before_answer_model_and_saves_reason(self):
        reviewer = Mock(return_value=UNSUPPORTED)
        ambiguous_timing = [{
            **CANDIDATES[0],
            "content": "退款申请完成后会尽快处理，具体到账时间以系统通知为准。",
        }]
        graph = self.build_graph(reviewer, ambiguous_timing)
        response = self.start(graph)
        self.assertEqual(response.tool_result, [])
        self.assertIn("没有找到足够证据", response.answer)
        self.assertEqual(self.gateway.request.call_count, 1)
        reviewer.assert_called_once_with("退款多久到账", ambiguous_timing)
        state = graph.get_state({"configurable": {"thread_id": "review-test"}}).values
        self.assertEqual(state["evidence_review"]["missing_information"], "没有到账时间")

    def test_supported_passes_only_checked_excerpts_to_answer_model(self):
        graph = self.build_graph(Mock(return_value=SUPPORTED))
        response = self.start(graph)
        self.assertIn("三个工作日", response.answer)
        self.assertNotIn("电子发票", response.answer)
        self.assertIn("来源：《退款政策》第1页", response.answer)
        self.assertEqual(self.gateway.request.call_count, 2)
        tool_message = self.gateway.request.call_args.args[1][-1]
        self.assertEqual(extract_context_chunk_ids(tool_message), ["refund-1"])
        self.assertNotIn("电子发票", tool_message["content"])
        self.assertEqual(response.tool_trace[0].result, CANDIDATES)

    def test_reviewer_cannot_mutate_source_to_legitimize_a_forged_quote(self):
        def mutate_then_claim(question, candidates):
            candidates[0]["content"] = "一小时到账"
            return {**SUPPORTED, "supporting_quotes": [{"chunk_id": "refund-1", "text": "一小时到账"}]}

        graph = self.build_graph(mutate_then_claim)
        with self.assertRaises(ValueError):
            self.start(graph)
        self.assertEqual(self.candidates, CANDIDATES)
        self.assertEqual(self.gateway.request.call_count, 1)

    def test_empty_results_skip_review(self):
        reviewer = Mock(return_value=SUPPORTED)
        response = self.start(self.build_graph(reviewer, []))
        reviewer.assert_not_called()
        self.assertIn("没有找到足够证据", response.answer)

    def test_hard_answer_type_conflict_skips_model_review(self):
        reviewer = Mock(return_value=SUPPORTED)
        steps_only = [{**CANDIDATES[0], "content": "打开订单详情页，点击申请退款。"}]
        response = self.start(self.build_graph(reviewer, steps_only))
        reviewer.assert_not_called()
        self.assertEqual(response.tool_result, [])

    def test_uncertain_paraphrase_can_be_recovered_by_review(self):
        candidate = [{
            **CANDIDATES[0],
            "content": "通过订单记录旁的售后入口发起请求，并补充原因后确认。",
        }]
        supported = {
            "supported": True,
            "supporting_quotes": [{
                "chunk_id": "refund-1", "text": candidate[0]["content"],
            }],
            "missing_information": "",
        }
        gateway = Mock(wraps=ConfiguredAgentModelGateway())
        graph = build_agent_graph(
            PendingActionStore(), gateway,
            tool_runner=lambda name, args: candidate,
            evidence_reviewer=Mock(return_value=supported),
            evidence_review_modes=frozenset({"mock"}),
        )
        response = start_agent_graph(graph, AgentGraphStartRequest(
            thread_id="paraphrase-review", message="如何办理退款", mode="mock",
        ))
        self.assertIn("售后入口", response.answer)
        self.assertEqual(response.tool_result[0]["chunk_id"], "refund-1")

    def test_review_failure_does_not_continue_to_answer_generation(self):
        graph = self.build_graph(Mock(side_effect=TimeoutError("核对超时")))
        with self.assertRaises(TimeoutError):
            self.start(graph)
        self.assertEqual(self.gateway.request.call_count, 1)
