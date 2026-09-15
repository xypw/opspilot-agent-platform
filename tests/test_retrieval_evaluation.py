"""检索评测测试：指标本身和方案对比都不调用真实模型或网络。"""

import unittest

from retrieval_evaluation import (
    calculate_mrr_at_k,
    calculate_recall_at_k,
    compare_retrievers,
    evaluate_retriever,
    extract_context_chunk_ids,
    find_context_drops,
)


class FakeEmbeddingService:
    """让语义检索故意把通用文档排第一，以验证混合检索是否改善结果。"""

    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0]


class FakeReranker:
    def score(self, query: str, documents: list[str]) -> list[float]:
        return [0.99 if "PAYMENT_403" in document else 0.01 for document in documents]


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.runs = [
            {
                "expected_chunk_id": "refund",
                "retrieved_chunk_ids": ["refund", "invoice"],
            },
            {
                "expected_chunk_id": "ticket",
                "retrieved_chunk_ids": ["invoice", "ticket"],
            },
            {
                "expected_chunk_id": "order",
                "retrieved_chunk_ids": ["invoice", "ticket"],
            },
        ]

    def test_recall_at_k_counts_any_hit_in_the_cutoff(self):
        self.assertAlmostEqual(calculate_recall_at_k(self.runs, 1), 1 / 3)
        self.assertAlmostEqual(calculate_recall_at_k(self.runs, 2), 2 / 3)

    def test_mrr_rewards_a_higher_rank(self):
        # 三条的贡献分别为 1、1/2、0，平均值为 0.5。
        self.assertAlmostEqual(calculate_mrr_at_k(self.runs, 2), 0.5)

    def test_mrr_ignores_result_outside_k(self):
        self.assertAlmostEqual(calculate_mrr_at_k(self.runs, 1), 1 / 3)

    def test_evaluate_retriever_keeps_per_case_results(self):
        cases = [{"id": "refund", "query": "退款", "expected_chunk_id": "refund"}]
        summary = evaluate_retriever(
            "fake",
            lambda query, k: [{"chunk_id": "refund"}],
            cases,
            k=1,
        )

        self.assertEqual(summary["recall_at_k"], 1.0)
        self.assertEqual(summary["mrr_at_k"], 1.0)
        self.assertEqual(summary["runs"][0]["case_id"], "refund")

    def test_context_drop_diagnostic_distinguishes_pipeline_stages(self):
        runs = [
            {
                "case_id": "lost-after-retrieval",
                "expected_chunk_id": "urgent-refund",
                "candidate_chunk_ids": ["urgent-refund", "normal-refund"],
                "context_chunk_ids": ["normal-refund"],
            },
            {
                "case_id": "never-retrieved",
                "expected_chunk_id": "invoice",
                "candidate_chunk_ids": ["normal-refund"],
                "context_chunk_ids": ["normal-refund"],
            },
            {
                "case_id": "kept-for-model",
                "expected_chunk_id": "normal-refund",
                "candidate_chunk_ids": ["normal-refund"],
                "context_chunk_ids": ["normal-refund"],
            },
        ]

        self.assertEqual(find_context_drops(runs), ["lost-after-retrieval"])

    def test_context_drop_diagnostic_rejects_missing_stage_trace(self):
        with self.assertRaisesRegex(ValueError, "context_chunk_ids"):
            find_context_drops([{
                "case_id": "missing-context",
                "expected_chunk_id": "refund",
                "candidate_chunk_ids": ["refund"],
            }])

    def test_context_ids_come_from_the_model_bound_tool_message(self):
        message = {
            "role": "tool",
            "content": '[{"chunk_id": "refund-1", "content": "审核通过后到账"}]',
        }
        self.assertEqual(extract_context_chunk_ids(message), ["refund-1"])

    def test_context_ids_reject_malformed_tool_message(self):
        with self.assertRaisesRegex(ValueError, "有效 JSON"):
            extract_context_chunk_ids({"role": "tool", "content": "not-json"})

    def test_comparison_shows_hybrid_and_rerank_can_improve_a_bad_semantic_rank(self):
        records = [
            {
                "chunk_id": "generic",
                "title": "支付说明",
                "content": "支付失败时请检查网络。",
                "embedding": [1.0, 0.0],
            },
            {
                "chunk_id": "payment-403",
                "title": "错误码手册",
                "content": "错误码 PAYMENT_403 表示支付权限不足。",
                "embedding": [0.0, 1.0],
            },
        ]
        cases = [
            {
                "id": "payment-403",
                "query": "PAYMENT_403 是什么错误",
                "expected_chunk_id": "payment-403",
            }
        ]

        summaries = compare_retrievers(
            cases,
            records,
            FakeEmbeddingService(),
            reranker=FakeReranker(),
            k=1,
        )
        by_name = {summary["retriever"]: summary for summary in summaries}

        self.assertEqual(by_name["semantic"]["recall_at_k"], 0.0)
        self.assertEqual(by_name["hybrid_rrf"]["recall_at_k"], 1.0)
        self.assertEqual(by_name["hybrid_rrf_rerank"]["mrr_at_k"], 1.0)


if __name__ == "__main__":
    unittest.main()
