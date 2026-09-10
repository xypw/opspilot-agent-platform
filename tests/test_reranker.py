import unittest

from reranker import rerank_candidates


class FakeReranker:
    def score(self, query: str, documents: list[str]) -> list[float]:
        return [0.95 if "三个工作日" in document else 0.1 for document in documents]


class WrongCountReranker:
    def score(self, query: str, documents: list[str]) -> list[float]:
        return []


class RerankerTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {
                "chunk_id": "invoice",
                "title": "发票指南",
                "content": "订单完成后可以申请发票。",
                "rrf_score": 0.04,
            },
            {
                "chunk_id": "refund",
                "title": "退款制度",
                "content": "退款审核通过后三个工作日到账。",
                "rrf_score": 0.03,
            },
        ]

    def test_reranker_can_correct_recall_order(self):
        results = rerank_candidates(
            "退款多久到账",
            self.candidates,
            FakeReranker(),
            limit=1,
        )

        self.assertEqual(results[0]["chunk_id"], "refund")
        self.assertEqual(results[0]["rerank_score"], 0.95)

    def test_source_candidates_are_not_modified(self):
        rerank_candidates("退款多久到账", self.candidates, FakeReranker())
        self.assertNotIn("rerank_score", self.candidates[0])

    def test_empty_candidates_do_not_call_service(self):
        class FailingReranker:
            def score(self, query, documents):
                raise AssertionError("空候选不应调用模型")

        self.assertEqual(rerank_candidates("退款", [], FailingReranker()), [])

    def test_wrong_score_count_is_rejected(self):
        with self.assertRaises(ValueError):
            rerank_candidates("退款", self.candidates, WrongCountReranker())


if __name__ == "__main__":
    unittest.main()
