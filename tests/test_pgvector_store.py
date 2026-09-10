"""pgvector 语义检索单元测试：验证 SQL、排序和输出契约。"""

import unittest

from pgvector_store import PostgresVectorStore


class FakeEmbeddingService:
    def embed_query(self, query: str) -> list[float]:
        return [0.1, 0.9]


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None
        self.row_factory = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self, *, row_factory):
        self.fake_cursor.row_factory = row_factory
        return self.fake_cursor


class PostgresVectorStoreTests(unittest.TestCase):
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
