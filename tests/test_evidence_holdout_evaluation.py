"""独立证据集评测器测试。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from preview_tool_call import ModelAPIError

from evidence_holdout_evaluation import evaluate_evidence_holdout


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvidenceHoldoutEvaluationTests(unittest.TestCase):
    def test_rules_mode_reports_false_rejection_and_false_admission(self) -> None:
        cases = [
            {
                "case_id": "positive",
                "question": "如何办理退款？",
                "candidate": {
                    "chunk_id": "p1",
                    "content": "退款流程：点击退款，填写原因并提交。",
                },
                "sufficient": True,
            },
            {
                "case_id": "negative",
                "question": "退款多久能到账？",
                "candidate": {
                    "chunk_id": "n1",
                    "content": "如有退款问题，请联系人工客服。",
                },
                "sufficient": False,
            },
        ]

        report = evaluate_evidence_holdout(cases)

        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["counts"], {"tp": 1, "fn": 0, "fp": 0, "tn": 1})
        self.assertEqual(report["reviewer_calls"], 0)

    def test_hard_conflict_skips_reviewer(self) -> None:
        calls = []

        def reviewer(question: str, candidates: list[dict]) -> dict:
            calls.append((question, candidates))
            return {"supported": True, "evidence": []}

        cases = [
            {
                "case_id": "identifier-conflict",
                "question": "PAYMENT_409 表示什么？",
                "candidate": {
                    "chunk_id": "wrong-code",
                    "content": "PAYMENT_503 表示支付服务暂时不可用。",
                },
                "sufficient": False,
            }
        ]

        report = evaluate_evidence_holdout(cases, reviewer)

        self.assertEqual(report["counts"]["tn"], 1)
        self.assertEqual(report["reviewer_calls"], 0)
        self.assertEqual(calls, [])

    def test_admitted_paraphrase_still_requires_review_in_live_mode(self) -> None:
        def reviewer(_question: str, candidates: list[dict]) -> dict:
            return {
                "supported": True,
                "supporting_quotes": [
                    {
                        "chunk_id": candidates[0]["chunk_id"],
                        "text": "发起款项返还请求并补充原因后确认",
                    }
                ],
                "missing_information": "",
            }

        cases = [
            {
                "case_id": "paraphrase",
                "question": "如何办理退款？",
                "candidate": {
                    "chunk_id": "refund-paraphrase",
                    "content": "在订单记录旁找到售后入口，发起款项返还请求并补充原因后确认。",
                },
                "sufficient": True,
            }
        ]

        report = evaluate_evidence_holdout(cases, reviewer)

        self.assertEqual(report["counts"]["tp"], 1)
        self.assertEqual(report["uncertain_cases"], 0)
        self.assertEqual(report["results"][0]["gate_reason"], "matched_required_facts")
        self.assertEqual(report["reviewer_calls"], 1)

    def test_ambiguous_topic_still_reaches_reviewer(self) -> None:
        content = "从记录旁的售后入口发起请求，再补充原因并确认。"
        cases = [{"case_id": "unknown-phrasing", "question": "如何办理退款？",
                  "candidate": {"chunk_id": "p", "content": content}, "sufficient": True}]
        reviewer = Mock(return_value={"supported": True, "missing_information": "",
                                     "supporting_quotes": [{"chunk_id": "p", "text": content}]})
        report = evaluate_evidence_holdout(cases, reviewer)
        self.assertEqual(report["uncertain_cases"], 1)
        self.assertEqual(report["counts"]["tp"], 1)
        reviewer.assert_called_once()

    def test_reviewer_cannot_fabricate_quote(self) -> None:
        def reviewer(_question: str, _candidates: list[dict]) -> dict:
            return {
                "supported": True,
                "supporting_quotes": [
                    {"chunk_id": "refund", "text": "原文中不存在的结论"}
                ],
                "missing_information": "",
            }

        cases = [
            {
                "case_id": "fabricated-quote",
                "question": "退款多久能到账？",
                "candidate": {
                    "chunk_id": "refund",
                    "content": "退款审核通过后三个工作日原路到账。",
                },
                "sufficient": True,
            }
        ]

        with self.assertRaisesRegex(ValueError, "不是候选片段中的原文"):
            evaluate_evidence_holdout(cases, reviewer)

    def test_default_holdout_dataset_is_balanced_and_unique(self) -> None:
        path = PROJECT_ROOT / "evaluation_data" / "evidence_holdout_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))

        case_ids = [case["case_id"] for case in cases]
        positive_count = sum(case["sufficient"] for case in cases)

        self.assertEqual(len(cases), 20)
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertEqual(positive_count, 10)
        self.assertEqual(len(cases) - positive_count, 10)

    def test_records_upstream_error_and_continues_without_calling_it_a_success(self):
        candidate = {"chunk_id": "refund", "content": "退款三个工作日到账。"}
        cases = [{"case_id": str(i), "question": "退款多久到账？",
                  "candidate": candidate, "sufficient": False} for i in range(2)]
        error = ModelAPIError(429, "1302")
        error.model_http_attempts = 3
        reviewer = Mock(side_effect=[error, {
            "supported": False, "supporting_quotes": [], "missing_information": "信息不足",
        }])
        journal = []
        report = evaluate_evidence_holdout(
            cases, reviewer, capture_review_errors=True, on_result=journal.append
        )
        self.assertEqual(report["review_errors"], 1)
        self.assertEqual(report["valid_correct_cases"], 1)
        self.assertEqual(report["model_http_attempts"], 3)
        self.assertEqual(len(journal), 2)
        self.assertEqual(journal[0]["error"]["status_code"], 429)
        self.assertEqual(journal[0]["error"]["provider_error_code"], "1302")

    def test_reviewer_cannot_mutate_evidence_to_make_quote_valid(self):
        candidate = {"chunk_id": "refund", "content": "退款三个工作日到账。"}
        cases = [{"case_id": "x", "question": "退款多久到账？",
                  "candidate": candidate, "sufficient": True}]
        def mutate(question, candidates):
            candidates[0]["content"] = "退款一小时到账。"
            return {"supported": True, "missing_information": "",
                    "supporting_quotes": [{"chunk_id": "refund", "text": "退款一小时到账。"}]}
        report = evaluate_evidence_holdout(cases, mutate, capture_review_errors=True)
        self.assertEqual(report["review_errors"], 1)
        self.assertEqual(report["counts"]["fn"], 1)
        self.assertEqual(candidate["content"], "退款三个工作日到账。")


if __name__ == "__main__":
    unittest.main()
