"""pgvector 语义检索单元测试：验证 SQL、排序和输出契约。"""

import unittest

from pgvector_store import PostgresVectorStore


class FakeEmbeddingService:
    def __init__(self, document_vectors=None):
        self.document_vectors = document_vectors
        self.embedded_texts = None

    def embed_query(self, query: str) -> list[float]:
        return [0.1, 0.9]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts = texts
        return self.document_vectors if self.document_vectors is not None else [[0.1, 0.9] for _ in texts]


class FakeCursor:
    def __init__(self, rows, fail_on_call=None):
        self.rows = rows
        self.sql = None
        self.params = None
        self.row_factory = None
        self.calls = []
        self.fail_on_call = fail_on_call

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        self.calls.append((sql, params))
        if len(self.calls) == self.fail_on_call:
            raise RuntimeError("第七个片段写入失败")

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False

    def cursor(self, *, row_factory=None):
        self.fake_cursor.row_factory = row_factory
        return self.fake_cursor


class PostgresVectorStoreTests(unittest.TestCase):
    @staticmethod
    def sample_chunks(count=2):
        return [
            {"chunk_id": f"refund-p1-c{index}", "page": 1, "content": f"片段 {index}"}
            for index in range(count)
        ]

    def test_save_document_writes_parent_then_all_chunks_in_one_connection(self):
        cursor = FakeCursor([])
        connection = FakeConnection(cursor)
        embedding = FakeEmbeddingService()
        store = PostgresVectorStore(embedding, lambda: connection, dimensions=2)

        saved = store.save_document("refund", "退款制度", "refund.pdf", 1, self.sample_chunks())

        self.assertEqual(saved, 2)
        self.assertEqual(embedding.embedded_texts, ["片段 0", "片段 1"])
        self.assertEqual(len(cursor.calls), 3)
        self.assertIn("INSERT INTO documents", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], ("refund", "退款制度", "refund.pdf", 1))
        self.assertIn("INSERT INTO knowledge_chunks", cursor.calls[1][0])
        self.assertIn("%s::vector", cursor.calls[1][0])
        self.assertEqual(cursor.calls[2][1],
                         ("refund-p1-c1", "refund", 1, 1, "片段 1", "[0.1,0.9]"))
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)

    def test_seventh_chunk_failure_propagates_out_of_transaction(self):
        cursor = FakeCursor([], fail_on_call=8)
        connection = FakeConnection(cursor)
        store = PostgresVectorStore(FakeEmbeddingService(), lambda: connection, dimensions=2)

        with self.assertRaisesRegex(RuntimeError, "第七个片段"):
            store.save_document("refund", "退款制度", "refund.pdf", 1, self.sample_chunks(7))

        self.assertEqual(len(cursor.calls), 8)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_invalid_document_vector_does_not_open_database_connection(self):
        embedding = FakeEmbeddingService(document_vectors=[[0.1]])
        store = PostgresVectorStore(
            embedding,
            connection_factory=lambda: self.fail("无效向量不应连接数据库"),
            dimensions=2,
        )

        with self.assertRaisesRegex(ValueError, "2 维"):
            store.save_document("refund", "退款制度", "refund.pdf", 1, self.sample_chunks(1))

    def test_search_uses_similarity_desc_and_parameterized_values(self):
        cursor = FakeCursor([
            {
                "chunk_id": "refund-p1-c0",
                "title": "退款制度",
                "page": 1,
                "content": "退款会在三个工作日内到账。",
                "similarity": 0.91,
            }
        ])
        store = PostgresVectorStore(
            FakeEmbeddingService(),
            connection_factory=lambda: FakeConnection(cursor),
            dimensions=2,
        )

        results = store.search("退款多久到账", limit=1)

        self.assertIn("1 - (chunk.embedding <=> %s::vector)", cursor.sql)
        self.assertIn("ORDER BY similarity DESC", cursor.sql)
        self.assertEqual(cursor.params, ("[0.1,0.9]", 1))
        self.assertEqual(results[0]["score"], 0.91)
        self.assertNotIn("embedding", results[0])

    def test_invalid_query_limit_and_vector_dimensions_are_rejected(self):
        store = PostgresVectorStore(
            FakeEmbeddingService(),
            connection_factory=lambda: self.fail("无效输入不应连接数据库"),
            dimensions=3,
        )

        with self.assertRaisesRegex(ValueError, "query"):
            store.search("   ")
        with self.assertRaisesRegex(ValueError, "limit"):
            store.search("退款", 0)
        with self.assertRaisesRegex(ValueError, "3 维"):
            store.search("退款")


if __name__ == "__main__":
    unittest.main()
