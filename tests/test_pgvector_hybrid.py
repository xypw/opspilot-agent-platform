"""持久化混合检索的离线契约测试；不使用真实 Embedding/Reranker。"""

import unittest
from unittest.mock import Mock

from keyword_search import search_terms
from pgvector_store import PostgresVectorStore
from reranker import RerankerUnavailableError
from tests.test_pgvector_store import FakeConnection, FakeCursor, FakeEmbeddingService


class TwoLaneCursor(FakeCursor):
    def __init__(self, semantic, lexical):
        super().__init__([])
        self.lanes = [semantic, lexical]

    def fetchall(self):
        return self.lanes.pop(0)


def candidate(identifier, similarity=0.9, content="退款三个工作日到账", **extra):
    return {"chunk_id": identifier, "title": "售后政策", "page": 2,
            "content": content, "semantic_score": similarity,
            "source_start": 10, "source_end": 21, "heading_path": ["退款"],
            "parser_version": "test", **extra}


class PostgresHybridTests(unittest.TestCase):
    def store(self, semantic, lexical):
        self.cursor = TwoLaneCursor(semantic, lexical)
        self.embedding = Mock(wraps=FakeEmbeddingService())
        return PostgresVectorStore(self.embedding, lambda: FakeConnection(self.cursor), dimensions=2)

    def test_both_lanes_use_publication_filters_parameters_and_bounded_candidates(self):
        store = self.store([candidate("a")], [candidate("a", keyword_score=2)])
        results = store.search_hybrid("退款多久到账", 2)
        self.assertEqual([r["chunk_id"] for r in results], ["a"])
        self.embedding.embed_query.assert_called_once()
        self.assertEqual(len(self.cursor.calls), 3)
        self.assertIn("REPEATABLE READ", self.cursor.calls[0][0])
        for sql, args in self.cursor.calls[1:]:
            self.assertIn("chunk.publication_status = 'published'", sql)
            self.assertIn("document.publication_status IN", sql)
            self.assertIn("LIMIT %s", sql)
            self.assertNotIn("退款多久到账", sql)
            self.assertEqual(args[-1], 6)
        self.assertIn("chunk.lexical_terms && %s::text[]", self.cursor.calls[2][0])
        self.assertEqual(self.cursor.calls[2][1][1], sorted(search_terms("退款多久到账")))
        self.assertEqual(results[0]["source_start"], 10)
        self.assertNotIn("embedding", results[0])

    def test_lexical_candidate_outside_vector_top_k_uses_its_own_similarity(self):
        store = self.store([candidate("vector")], [candidate("lexical", 0.85, keyword_score=3)])
        self.assertEqual({r["chunk_id"] for r in store.search_hybrid("退款")}, {"vector", "lexical"})

    def test_weak_keyword_overlap_cannot_bypass_semantic_threshold(self):
        store = self.store([candidate("weak", 0.2)], [candidate("weak", 0.2, keyword_score=10)])
        self.assertEqual(store.search_hybrid("退款"), [])

    def test_only_full_identifier_match_has_exception(self):
        store = self.store([], [
            candidate("exact", 0.1, "E_REFUND_TIMEOUT 表示退款超时", keyword_score=1),
            candidate("partial", 0.1, "E_REFUND_TIMEOUT_EXTRA", keyword_score=1)])
        self.assertEqual([r["chunk_id"] for r in store.search_hybrid("E_REFUND_TIMEOUT")], ["exact"])

    def test_reranker_reorders_fused_candidates_and_preserves_citations(self):
        store = self.store([candidate("a"), candidate("b")], [])
        reranker = Mock()
        reranker.score.return_value = [0.1, 0.95]
        result = store.search_hybrid("退款", 1, reranker=reranker)
        self.assertEqual(result[0]["chunk_id"], "b")
        self.assertEqual(result[0]["page"], 2)
        self.assertEqual(result[0]["score"], 0.95)
        self.assertEqual(len(reranker.score.call_args.args[1]), 2)

    def test_unavailable_reranker_falls_back_without_repeating_retrieval(self):
        store = self.store([candidate("a"), candidate("b")], [])
        reranker = Mock()
        reranker.score.side_effect = RerankerUnavailableError("offline")
        result = store.search_hybrid("退款", 1, reranker=reranker)
        self.assertEqual(result[0]["chunk_id"], "a")
        self.embedding.embed_query.assert_called_once()
        self.assertEqual(len(self.cursor.calls), 3)

    def test_programming_error_is_not_silently_downgraded(self):
        store = self.store([candidate("a")], [])
        reranker = Mock()
        reranker.score.side_effect = TypeError("bug")
        with self.assertRaises(TypeError):
            store.search_hybrid("退款", reranker=reranker)

    def test_nonfinite_similarity_cannot_enter_evidence(self):
        store = self.store([candidate("a", float("nan"))], [candidate("b", float("inf"))])
        self.assertEqual(store.search_hybrid("退款"), [])

    def test_invalid_inputs_do_not_call_embedding_or_database(self):
        store = self.store([], [])
        for query, limit in [("", 3), ("退款", 0), ("退款", True), ("退款", 101)]:
            with self.assertRaises(ValueError):
                store.search_hybrid(query, limit)
        self.embedding.embed_query.assert_not_called()
        self.assertEqual(self.cursor.calls, [])

    def test_both_publication_paths_write_same_lexical_token_contract(self):
        store = self.store([], [])
        store.save_document("new", "退款规则", "policy.pdf", 1,
                            [{"chunk_id": "new-c0", "content": "E_REFUND 超时", "page": 1}])
        sql, params = self.cursor.calls[-1]
        self.assertIn("lexical_terms", sql)
        self.assertEqual(params[-1], sorted(search_terms("退款规则 E_REFUND 超时")))


if __name__ == "__main__":
    unittest.main()
