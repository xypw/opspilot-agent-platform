import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evidence_review import ConfiguredEvidenceReviewer, EvidenceReviewReply
from preview_tool_call import ModelAPIError
from retry_policy import ModelRequestTelemetry
from scripts import run_bounded_evidence_batch as batch


def case(index, *, sufficient=True):
    return {"case_id": f"case-{index}", "question": "退款多久到账？",
            "candidate": {"chunk_id": f"chunk-{index}",
                          "content": f"第{index}项演示政策：退款三个工作日到账。"},
            "sufficient": sufficient, "rationale": "LOCAL_ONLY_EXPECTED_LABEL"}


def positive(question, candidates, *, attempts=1):
    candidate = candidates[0]
    return EvidenceReviewReply(
        verdict={"supported": True, "supporting_quotes": [{"chunk_id": candidate["chunk_id"],
                   "text": candidate["content"]}], "missing_information": ""},
        telemetry=ModelRequestTelemetry(http_attempts=attempts),
    )


class BoundedEvidenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.regression, self.holdout = self.root / "regression.json", self.root / "holdout.json"
        self.output = self.root / "report.json"
        self.save([case(1)], [case(2)])

    def save(self, regression, holdout):
        self.regression.write_text(json.dumps(regression, ensure_ascii=False), encoding="utf-8")
        self.holdout.write_text(json.dumps(holdout, ensure_ascii=False), encoding="utf-8")

    def run_batch(self, reviewer, **kwargs):
        return batch.run_batch(self.regression, self.holdout, self.output,
                               reviewer=reviewer, pause=Mock(), **kwargs)

    def test_actual_budget_and_three_attempt_reservation_apply_across_groups(self):
        self.save([case(1), case(2)], [case(3), case(4)])
        attempts = iter([1, 3, 3])
        reviewer = Mock(side_effect=lambda q, c: positive(q, c, attempts=next(attempts)))
        report = self.run_batch(reviewer, max_http_attempts=8)
        self.assertEqual(reviewer.call_count, 3)
        self.assertEqual(report["summary"]["model_http_attempts"], 7)
        self.assertEqual(report["groups"]["regression"]["counts"]["tp"], 2)
        self.assertEqual(report["groups"]["holdout"]["counts"]["tp"], 1)
        self.assertEqual(report["groups"]["holdout"]["not_run_case_ids"], ["case-4"])
        self.assertFalse(report["summary"]["all_passed"])

    def test_exhausted_429_stops_both_groups_and_error_is_not_tn(self):
        self.save([case(1, sufficient=False), case(2)], [case(3)])
        error = ModelAPIError(429, "1302")
        error.model_http_attempts = 3
        reviewer = Mock(side_effect=error)
        report = self.run_batch(reviewer)
        reviewer.assert_called_once()
        self.assertEqual(report["summary"]["stop_reason"], "provider_429_after_retries")
        self.assertEqual(report["summary"]["not_run_cases"], 2)
        self.assertEqual(report["groups"]["regression"]["counts"]["tn"], 0)
        self.assertEqual(report["groups"]["regression"]["run_errors"], 1)
        self.assertEqual(report["results"][0]["outcome"], "run_error")

    def test_manifest_precedes_calls_journal_is_incremental_and_no_overwrite(self):
        calls = []

        def reviewer(question, candidates):
            manifest = json.loads(self.output.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["datasets"]["regression"]["sha256"],
                             hashlib.sha256(self.regression.read_bytes()).hexdigest())
            self.assertIn("evidence_review.py", manifest["source_sha256"])
            prior = self.output.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(prior), len(calls))
            calls.append(question)
            return positive(question, candidates)

        report = self.run_batch(reviewer)
        journal = [json.loads(line) for line in self.output.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(journal, report["results"])
        original = self.output.read_bytes()
        with self.assertRaises(ValueError):
            self.run_batch(Mock())
        self.assertEqual(self.output.read_bytes(), original)

    def test_validation_rejects_aliases_duplicate_ids_and_duplicate_inputs(self):
        with self.assertRaises(ValueError):
            batch.load_groups(self.regression, self.regression.parent / "." / self.regression.name)
        for changed in (case(1), {**case(1), "case_id": "different-id"},
                        {**case(2), "sufficient": "false"}, {**case(2), "extra": "field"}):
            with self.subTest(changed=changed):
                self.save([case(1)], [changed])
                reviewer = Mock()
                with self.assertRaises(ValueError):
                    self.run_batch(reviewer)
                reviewer.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_case_budget_and_fixed_configuration_fail_before_external_calls(self):
        reviewer = Mock()
        for budget in (0, 121, True):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.run_batch(reviewer, max_http_attempts=budget)
        self.save([case(i) for i in range(21)], [case(i) for i in range(21, 41)])
        with self.assertRaises(ValueError):
            self.run_batch(reviewer)
        self.save([case(1)], [case(2)])
        for target, value in (("MODEL", "paid-model"), ("API_URL", "https://other.invalid")):
            with patch.object(batch.preview_tool_call, target, value), self.assertRaises(ValueError):
                self.run_batch(reviewer)
        with patch.object(batch.evidence_review, "MAX_REVIEW_HTTP_ATTEMPTS", 4), self.assertRaises(ValueError):
            self.run_batch(reviewer)
        reviewer.assert_not_called()

    def test_real_retry_and_format_paths_count_each_http_and_do_not_leak_labels(self):
        sent = []

        def request(api_key, client, messages, **kwargs):
            sent.append(copy.deepcopy(messages))
            payload = json.loads(messages[1]["content"])
            self.assertEqual(set(payload), {"question", "candidates"})
            self.assertEqual(set(payload["candidates"][0]), {"chunk_id", "content"})
            self.assertNotIn("LOCAL_ONLY_EXPECTED_LABEL", json.dumps(messages))
            self.assertNotIn("sufficient", payload)
            if len(sent) == 1:
                raise ModelAPIError(503)
            if len(sent) == 2:
                return {"role": "assistant", "content": "malformed"}
            verdict = positive(payload["question"], payload["candidates"]).verdict
            return {"role": "assistant", "content": json.dumps(verdict)}

        with patch("evidence_review.load_api_key", return_value="synthetic"), patch(
            "retry_policy.request_message", side_effect=request
        ), patch("retry_policy.sleep"):
            report = self.run_batch(ConfiguredEvidenceReviewer(), max_http_attempts=5)
        self.assertEqual(len(sent), 3)
        self.assertEqual(report["summary"]["model_http_attempts"], 3)
        self.assertEqual(report["groups"]["holdout"]["not_run_cases"], 1)
        self.assertFalse(report["summary"]["all_passed"])

    def test_non_429_error_remains_separate_and_other_group_still_runs(self):
        self.save([case(1, sufficient=False)], [case(2)])
        error = ModelAPIError(401)
        error.model_http_attempts = 1
        reviewer = Mock(side_effect=[error, positive("", [case(2)["candidate"]])])
        report = self.run_batch(reviewer)
        self.assertEqual(report["summary"]["run_errors"], 1)
        self.assertEqual(report["groups"]["regression"]["valid_correct_cases"], 0)
        self.assertEqual(report["groups"]["regression"]["accuracy_on_valid_cases"], None)
        self.assertTrue(report["groups"]["holdout"]["all_passed"])
        self.assertFalse(report["summary"]["all_passed"])

    def test_insufficient_initial_budget_runs_nothing_and_cli_cannot_report_success(self):
        reviewer = Mock()
        report = self.run_batch(reviewer, max_http_attempts=2)
        reviewer.assert_not_called()
        self.assertEqual(report["summary"]["not_run_cases"], 2)
        self.assertEqual(report["summary"]["model_http_attempts"], 0)
        self.assertFalse(report["summary"]["all_passed"])
        with patch.object(batch, "run_batch", return_value=report), patch("builtins.print"):
            self.assertEqual(batch.main(["--regression-cases", str(self.regression),
                                        "--holdout-cases", str(self.holdout), "--output", str(self.output),
                                        "--allow-external-model"]), 1)


if __name__ == "__main__":
    unittest.main()
