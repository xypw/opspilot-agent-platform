"""单一相似度门槛的离线混淆矩阵测试。"""

import json
import unittest
from pathlib import Path

from evidence_gate_metrics import evaluate_thresholds


CASES_FILE = Path(__file__).resolve().parents[1] / "evaluation_data" / "evidence_gate_score_cases.json"


class EvidenceGateMetricsTests(unittest.TestCase):
    def test_recorded_scores_show_threshold_tradeoff(self):
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))

        self.assertEqual(evaluate_thresholds(cases, [0.50, 0.55, 0.57, 0.60]), [
            {"threshold": 0.50, "tp": 3, "fn": 0, "fp": 2, "tn": 1},
            {"threshold": 0.55, "tp": 2, "fn": 1, "fp": 1, "tn": 2},
            {"threshold": 0.57, "tp": 2, "fn": 1, "fp": 0, "tn": 3},
            {"threshold": 0.60, "tp": 1, "fn": 2, "fp": 0, "tn": 3},
        ])

    def test_equal_score_is_admitted(self):
        cases = [{"case_id": "boundary", "answerable": True, "top_score": 0.6}]

        self.assertEqual(evaluate_thresholds(cases, [0.6])[0]["tp"], 1)

    def test_rejects_invalid_label_and_score(self):
        for case in (
            {"case_id": "bad", "answerable": "yes", "top_score": 0.5},
            {"case_id": "bad", "answerable": True, "top_score": None},
            {"case_id": "bad", "answerable": True, "top_score": float("nan")},
        ):
            with self.subTest(case=case), self.assertRaises(ValueError):
                evaluate_thresholds([case], [0.5])

    def test_rejects_duplicate_ids_and_invalid_threshold(self):
        case = {"case_id": "same", "answerable": True, "top_score": 0.5}

        with self.assertRaises(ValueError):
            evaluate_thresholds([case, case], [0.5])
        with self.assertRaises(ValueError):
            evaluate_thresholds([case], [float("inf")])
        with self.assertRaises(ValueError):
            evaluate_thresholds([None], [0.5])


if __name__ == "__main__":
    unittest.main()
