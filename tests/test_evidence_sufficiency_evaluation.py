"""人工边界集与证据过滤规则的离线评测测试。"""

import json
import unittest
from pathlib import Path

from evidence_sufficiency_evaluation import evaluate_evidence_sufficiency


CASES_FILE = Path(__file__).resolve().parents[1] / "evaluation_data" / "evidence_sufficiency_cases.json"


class EvidenceSufficiencyEvaluationTests(unittest.TestCase):
    def test_labeled_boundary_cases_are_scored_without_locking_in_known_failures(self):
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))

        report = evaluate_evidence_sufficiency(cases)

        self.assertEqual(report["case_count"], 15)
        self.assertEqual(sum(report["counts"].values()), 15)
        self.assertEqual(
            {item["case_id"] for item in report["results"]},
            {case["case_id"] for case in cases},
        )
        # 正例和已解决的负例必须保持正确，但不把当前的误放行写成永久契约。
        outcomes = {item["case_id"]: item["outcome"] for item in report["results"]}
        self.assertEqual(outcomes["refund_online_steps"], "tp")
        self.assertEqual(outcomes["refund_counter_steps"], "tp")
        self.assertEqual(outcomes["refund_steps_from_eta"], "tn")
        self.assertEqual(outcomes["refund_steps_from_submitted_status"], "tn")

    def test_rejects_missing_label_and_duplicate_id(self):
        case = {"case_id": "one", "question": "如何退款", "candidate_content": "请提交退款申请。",
                "sufficient": True}

        with self.assertRaises(ValueError):
            evaluate_evidence_sufficiency([{**case, "sufficient": "yes"}])
        with self.assertRaises(ValueError):
            evaluate_evidence_sufficiency([case, case])

    def test_single_positive_and_negative(self):
        report = evaluate_evidence_sufficiency([
            {"case_id": "positive", "question": "如何申请退款",
             "candidate_content": "请提交退款申请。", "sufficient": True},
            {"case_id": "negative", "question": "如何申请退款",
             "candidate_content": "退款审核通过后三个工作日到账。", "sufficient": False},
        ])

        self.assertEqual(report["counts"], {"tp": 1, "fn": 0, "fp": 0, "tn": 1})


if __name__ == "__main__":
    unittest.main()
