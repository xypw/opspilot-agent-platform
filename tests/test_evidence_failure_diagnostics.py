"""失败阶段可诊断、配额拒绝不截断报告；所有模型请求均为替身。"""
import json
import unittest
from unittest.mock import Mock, patch

from evidence_holdout_evaluation import evaluate_evidence_holdout
from evidence_review import ConfiguredEvidenceReviewer
from model_rate_limit import ModelAdmissionUnavailable, ModelRateLimitExceeded
from preview_tool_call import ModelAPIError


CANDIDATE = {"chunk_id": "refund", "content": "退款三个工作日到账。"}
CASES = [{"case_id": str(i), "question": "退款多久到账？",
          "candidate": CANDIDATE, "sufficient": False} for i in range(2)]
REJECT = {"supported": False, "supporting_quotes": [], "missing_information": "缺少证据"}


class EvidenceFailureDiagnosticsTests(unittest.TestCase):
    def test_admission_errors_continue_journal_and_never_count_as_correct(self):
        for error in (ModelRateLimitExceeded(5), ModelAdmissionUnavailable()):
            with self.subTest(error=type(error).__name__):
                journal = []
                result = evaluate_evidence_holdout(
                    CASES, Mock(side_effect=[error, REJECT]),
                    capture_review_errors=True, on_result=journal.append,
                )
                self.assertEqual(result["review_errors"], 1)
                self.assertEqual(result["valid_correct_cases"], 1)
                self.assertEqual(result["model_http_attempts"], 0)
                self.assertEqual(len(journal), 2)
                self.assertEqual(journal[0]["error"]["stage"], "model_admission")

    def test_strict_mode_still_raises_admission_error(self):
        with self.assertRaises(ModelRateLimitExceeded):
            evaluate_evidence_holdout(CASES, Mock(side_effect=ModelRateLimitExceeded(5)))

    def test_retry_admission_failure_preserves_already_sent_attempt(self):
        error = ModelRateLimitExceeded(5)
        error.model_http_attempts = 1
        error.model_turn_durations_ms = [12.5]
        report = evaluate_evidence_holdout(
            CASES, Mock(side_effect=[error, REJECT]), capture_review_errors=True,
        )
        self.assertEqual(report["model_http_attempts"], 1)
        self.assertEqual(report["results"][0]["duration_ms"], 12.5)
        self.assertEqual(report["valid_correct_cases"], 1)

    def test_json_error_identifies_parse_stage_without_echoing_response(self):
        with patch("evidence_review.load_api_key", return_value="fake-key"), \
             patch("evidence_review.request_message_with_retry", return_value={
                 "role": "assistant", "content": "secret-response-not-json",
             }):
            result = evaluate_evidence_holdout(
                CASES[:1], ConfiguredEvidenceReviewer(), capture_review_errors=True,
            )
        self.assertEqual(result["results"][0]["error"]["stage"], "response_parse")
        self.assertNotIn("secret-response", json.dumps(result))

    def test_http_error_is_separate_from_parse_error(self):
        with patch("evidence_review.load_api_key", return_value="fake-key"), \
             patch("evidence_review.request_message_with_retry", side_effect=ModelAPIError(429, "1302")):
            result = evaluate_evidence_holdout(
                CASES[:1], ConfiguredEvidenceReviewer(), capture_review_errors=True,
            )
        error = result["results"][0]["error"]
        self.assertEqual(error["stage"], "model_request")
        self.assertEqual(error["status_code"], 429)
        self.assertEqual(error["provider_error_code"], "1302")

    def test_invalid_schema_or_quote_is_validation_failure(self):
        for verdict in ({"supported": "true"}, {
            "supported": True, "supporting_quotes": [{"chunk_id": "refund", "text": "一秒到账"}],
            "missing_information": "",
        }):
            with self.subTest(verdict=verdict):
                result = evaluate_evidence_holdout(
                    CASES[:1], Mock(return_value=verdict), capture_review_errors=True,
                )
                self.assertEqual(result["results"][0]["error"]["stage"], "verdict_validation")
                self.assertEqual(result["valid_correct_cases"], 0)

    def test_untrusted_exception_stage_is_not_copied_into_report(self):
        error = ValueError("secret-message")
        error.evidence_review_stage = "secret-stage"
        result = evaluate_evidence_holdout(CASES[:1], Mock(side_effect=error), capture_review_errors=True)
        self.assertEqual(result["results"][0]["error"]["stage"], "reviewer_call")
        self.assertNotIn("secret-", json.dumps(result))
