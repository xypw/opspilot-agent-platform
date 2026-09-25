import json
import unittest
from unittest.mock import patch

from evidence_review import ConfiguredEvidenceReviewer, EvidenceResponseFormatError, verify_evidence_review
from model_rate_limit import ModelRateLimitExceeded
from preview_tool_call import ModelAPIError


CANDIDATES = [{"chunk_id": "invoice", "content": "订单完成后在票据服务入口申请。"}]
VERDICT = {"supported": True, "supporting_quotes": [
    {"chunk_id": "invoice", "text": CANDIDATES[0]["content"]}], "missing_information": ""}
VALID = {"role": "assistant", "content": json.dumps(VERDICT)}
INVALID = {"role": "assistant", "content": "private-malformed-response"}


class EvidenceFormatRetryTests(unittest.TestCase):
    def run_review(self, replies):
        self.messages = []
        self.budgets = []
        replies = iter(replies)

        def request(api_key, client, messages, **kwargs):
            self.messages.append(messages)
            self.budgets.append(kwargs["max_attempts"])
            reply = next(replies)
            attempts = 1
            if isinstance(reply, tuple):
                reply, attempts = reply
            if not isinstance(reply, ModelRateLimitExceeded):
                kwargs["telemetry"].http_attempts = attempts
                kwargs["telemetry"].retry_count = attempts - 1
                kwargs["telemetry"].duration_ms = 7
                kwargs["telemetry"].attempt_durations_ms = [7]
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("evidence_review.load_api_key", return_value="synthetic"), patch(
            "evidence_review.request_message_with_retry", side_effect=request,
        ):
            return ConfiguredEvidenceReviewer()("如何开票？", CANDIDATES)

    def test_repairs_format_once_preserves_evidence_and_counts_both_requests(self):
        reply = self.run_review([INVALID, VALID])
        verify_evidence_review(reply.verdict, CANDIDATES)
        self.assertEqual(reply.telemetry.http_attempts, 2)
        self.assertEqual(reply.telemetry.retry_count, 1)
        self.assertEqual(reply.telemetry.duration_ms, 14)
        self.assertEqual(reply.telemetry.attempt_durations_ms, [7, 7])
        self.assertEqual(self.messages[0][1], self.messages[1][1])
        self.assertEqual(self.budgets, [3, 2])
        self.assertNotIn("private-malformed-response", json.dumps(self.messages))

    def test_second_invalid_response_stops_with_aggregated_error_telemetry(self):
        with self.assertRaises(EvidenceResponseFormatError) as caught:
            self.run_review([INVALID, INVALID])
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(caught.exception.model_http_attempts, 2)
        self.assertEqual(caught.exception.evidence_review_stage, "response_parse")
        self.assertNotIn("private-malformed-response", str(caught.exception))

    def test_transport_error_is_not_retried_by_format_layer(self):
        with self.assertRaises(ModelAPIError):
            self.run_review([ModelAPIError(429, "1302")])
        self.assertEqual(len(self.messages), 1)

    def test_transport_retries_and_format_correction_share_three_attempt_budget(self):
        reply = self.run_review([(INVALID, 2), VALID])
        self.assertEqual(self.budgets, [3, 1])
        self.assertEqual(reply.telemetry.http_attempts, 3)
        self.assertEqual(reply.telemetry.retry_count, 2)
        with self.assertRaises(EvidenceResponseFormatError) as caught:
            self.run_review([(INVALID, 3)])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(caught.exception.model_http_attempts, 3)

    def test_correction_transport_failure_keeps_previous_format_attempt(self):
        with self.assertRaises(ModelAPIError) as caught:
            self.run_review([INVALID, (ModelAPIError(429, "1302"), 2)])
        self.assertEqual(caught.exception.model_http_attempts, 3)
        self.assertEqual(caught.exception.evidence_review_stage, "model_request")

    def test_role_or_tool_violation_does_not_get_a_format_retry(self):
        for reply in ({"role": "tool", "content": "{}"},
                      {**INVALID, "tool_calls": [{"id": "forbidden"}]}):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                self.run_review([reply])
            self.assertEqual(len(self.messages), 1)

    def test_second_request_admission_denial_preserves_only_sent_attempt(self):
        with self.assertRaises(ModelRateLimitExceeded) as caught:
            self.run_review([INVALID, ModelRateLimitExceeded(5)])
        self.assertEqual(caught.exception.model_http_attempts, 1)
        self.assertEqual(caught.exception.model_retry_count, 0)

    def test_invalid_quote_remains_rejected_instead_of_retried_until_pass(self):
        fabricated = {**VERDICT, "supporting_quotes": [{"chunk_id": "invoice", "text": "编造的答案"}]}
        reply = self.run_review([{"role": "assistant", "content": json.dumps(fabricated)}])
        with self.assertRaises(ValueError):
            verify_evidence_review(reply.verdict, CANDIDATES)
        self.assertEqual(len(self.messages), 1)
