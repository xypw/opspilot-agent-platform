"""引用评测只核对来源，不把离线结果冒充模型回答质量。"""

import unittest

from citation_evaluation import evaluate_citation_sources


DOCUMENTS = [
    {"chunk_id": "urgent", "title": "紧急退款", "page": 1, "content": "审核通过后通常三小时到账。"},
    {"chunk_id": "normal", "title": "普通退款", "page": 2, "content": "审核通过后三个工作日到账。"},
]


class CitationEvaluationTests(unittest.TestCase):
    def test_counts_extra_and_missing_gold_sources_separately(self):
        runs = [
            {"case_id": "found", "expected_chunk_id": "urgent",
             "retrieved_chunk_ids": ["urgent", "normal"]},
            {"case_id": "missed", "expected_chunk_id": "urgent",
             "retrieved_chunk_ids": ["normal"]},
        ]

        report = evaluate_citation_sources(runs, DOCUMENTS, k=2)

        self.assertEqual(report["citation_count"], 3)
        self.assertEqual(report["gold_citation_count"], 1)
        self.assertAlmostEqual(report["gold_source_precision"], 1 / 3)
        self.assertEqual(report["gold_source_recall"], 0.5)
        self.assertEqual(report["results"][0]["non_gold_sources"], ["《普通退款》第2页"])
        self.assertFalse(report["results"][1]["gold_source_cited"])

    def test_rejects_unknown_sources_and_duplicate_case_ids(self):
        run = {"case_id": "one", "expected_chunk_id": "urgent",
               "retrieved_chunk_ids": ["normal"]}

        with self.assertRaisesRegex(ValueError, "检索片段编号无效"):
            evaluate_citation_sources([{**run, "retrieved_chunk_ids": ["unknown"]}], DOCUMENTS, 1)
        with self.assertRaisesRegex(ValueError, "case_id"):
            evaluate_citation_sources([run, run], DOCUMENTS, 1)


if __name__ == "__main__":
    unittest.main()
