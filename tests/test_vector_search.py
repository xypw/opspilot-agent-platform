"""语义检索排序测试；不加载真实模型。"""

import unittest

from vector_search import semantic_search


class FakeQueryEmbeddingService:
    def embed_query(self, query):
        return [1.0, 0.0]


class VectorSearchTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeQueryEmbeddingService()
        self.records = [
            {
                "chunk_id": "invoice-p1-c0",
                "content": "订单完成后可以申请电子发票",
                "embedding": [0.1, 0.9],
            },
            {
                "chunk_id": "refund-p2-c0",
                "content": "退款三个工作日到账",
                "embedding": [0.8, 0.2],
            },
        ]

    def test_highest_similarity_is_first(self):
        results = semantic_search("钱什么时候退回来", self.records, self.service)

        self.assertEqual([item["chunk_id"] for item in results], [
            "refund-p2-c0", "invoice-p1-c0",
        ])
        self.assertGreater(results[0]["semantic_score"], results[1]["semantic_score"])

    def test_limit_returns_top_k(self):
        results = semantic_search("退款", self.records, self.service, limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "refund-p2-c0")

    def test_min_similarity_filters_weak_candidates_before_top_k(self):
        results = semantic_search(
            "退款", self.records, self.service,
            min_similarity=0.6,
        )
        self.assertEqual([item["chunk_id"] for item in results], ["refund-p2-c0"])
        self.assertEqual(
            semantic_search("退款", self.records, self.service, min_similarity=0.99),
            [],
        )

    def test_response_does_not_expose_large_embedding(self):
        result = semantic_search("退款", self.records, self.service, limit=1)[0]
        self.assertNotIn("embedding", result)
        self.assertIn("embedding", self.records[0], "不能修改原始索引记录")

    def test_invalid_input_stops_before_scoring(self):
        for query, limit in [("", 1), ("   ", 1), ("退款", 0), ("退款", True)]:
            with self.subTest(query=query, limit=limit):
                with self.assertRaises(ValueError):
                    semantic_search(query, self.records, self.service, limit)

    def test_wrong_dimension_is_rejected(self):
        records = [{"content": "错误向量", "embedding": [1.0, 0.0, 0.0]}]
        with self.assertRaisesRegex(ValueError, "维度不一致"):
            semantic_search("退款", records, self.service)

    def test_invalid_similarity_threshold_is_rejected(self):
        for threshold in (True, float("nan"), float("inf"), -1.1, 1.1, "0.6"):
            with self.subTest(threshold=threshold), self.assertRaisesRegex(ValueError, "min_similarity"):
                semantic_search("退款", self.records, self.service, min_similarity=threshold)


if __name__ == "__main__":
    unittest.main()
