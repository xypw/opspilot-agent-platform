import unittest

from vector_store import InMemoryVectorStore


class CountingEmbeddingService:
    def __init__(self):
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        vectors = {
            "退款通常在三个工作日内到账": [1.0, 0.0],
            "发票可以在订单完成后申请": [0.0, 1.0],
        }
        return [vectors[text] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        self.query_calls += 1
        return [1.0, 0.0]


class InMemoryVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.service = CountingEmbeddingService()
        self.store = InMemoryVectorStore(self.service)
        self.records = [
            {"id": "refund", "content": "退款通常在三个工作日内到账"},
            {"id": "invoice", "content": "发票可以在订单完成后申请"},
        ]

    def test_documents_are_embedded_when_added_not_when_searched(self):
        self.store.add(self.records)
        self.store.search("退款多久到账")
        self.store.search("钱什么时候退回")

        self.assertEqual(self.service.document_calls, 1)
        self.assertEqual(self.service.query_calls, 2)

    def test_search_reuses_stored_vectors_and_returns_best_match(self):
        self.store.add(self.records)

        result = self.store.search("退款多久到账", limit=1)

        self.assertEqual(result[0]["id"], "refund")
        self.assertNotIn("embedding", result[0])

    def test_add_does_not_put_embedding_on_callers_records(self):
        self.store.add(self.records)

        self.assertNotIn("embedding", self.records[0])
        self.assertIn("embedding", self.store.records[0])

    def test_empty_store_returns_no_results_without_embedding_query(self):
        self.assertEqual(self.store.search("退款"), [])
        self.assertEqual(self.service.query_calls, 0)


if __name__ == "__main__":
    unittest.main()
