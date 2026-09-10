import unittest

from hybrid_search import hybrid_search, reciprocal_rank_fusion
from keyword_search import keyword_search


class FakeEmbeddingService:
    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0]


class HybridSearchTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "chunk_id": "generic",
                "title": "支付问题",
                "page": 1,
                "content": "支付失败时请检查网络后重试。",
                "embedding": [1.0, 0.0],
            },
            {
                "chunk_id": "payment-403",
                "title": "错误码手册",
                "page": 4,
                "content": "错误码 PAYMENT_403 表示支付权限不足。",
                "embedding": [0.0, 1.0],
            },
        ]

    def test_keyword_search_matches_exact_error_code(self):
        results = keyword_search("PAYMENT_403", self.records)

        self.assertEqual(results[0]["chunk_id"], "payment-403")
        self.assertEqual(results[0]["keyword_score"], 1)
        self.assertNotIn("embedding", results[0])

    def test_rrf_accumulates_score_from_two_ranked_lists(self):
        semantic = [
            {"chunk_id": "a", "content": "A"},
            {"chunk_id": "b", "content": "B"},
        ]
        keyword = [
            {"chunk_id": "b", "content": "B"},
        ]

        results = reciprocal_rank_fusion([semantic, keyword], limit=2)

        self.assertEqual(results[0]["chunk_id"], "b")
        self.assertAlmostEqual(results[0]["rrf_score"], 1 / 62 + 1 / 61)

    def test_hybrid_search_promotes_exact_code_despite_lower_semantic_rank(self):
        results = hybrid_search(
            "PAYMENT_403",
            self.records,
            FakeEmbeddingService(),
            limit=1,
        )

        self.assertEqual(results[0]["chunk_id"], "payment-403")
        self.assertIn("semantic_score", results[0])
        self.assertIn("keyword_score", results[0])
        self.assertIn("rrf_score", results[0])

    def test_invalid_fusion_parameters_are_rejected(self):
        for limit, rank_constant in [(0, 60), (True, 60), (1, 0), (1, True)]:
            with self.subTest(limit=limit, rank_constant=rank_constant):
                with self.assertRaises(ValueError):
                    reciprocal_rank_fusion([], limit, rank_constant)


if __name__ == "__main__":
    unittest.main()
