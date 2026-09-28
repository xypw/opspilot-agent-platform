"""pgvector 语义检索单元测试：验证 SQL、排序和输出契约。"""

import unittest

from pgvector_store import DocumentIdConflictError, PostgresVectorStore


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
    def __init__(self, rows, fail_on_call=None, fetchone_results=None):
        self.rows = rows
        self.sql = None
        self.params = None
        self.row_factory = None
        self.calls = []
        self.fail_on_call = fail_on_call
        self.fetchone_results = list(fetchone_results or [])

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

    def fetchone(self):
        return self.fetchone_results.pop(0) if self.fetchone_results else None


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
        self.assertEqual(len(cursor.calls), 5)
        self.assertIn("pg_advisory_xact_lock", cursor.calls[0][0])
        self.assertIn("SELECT id FROM document_review_queue", cursor.calls[1][0])
        self.assertIn("INSERT INTO documents", cursor.calls[2][0])
        self.assertEqual(cursor.calls[2][1][:4], ("refund", "退款制度", "refund.pdf", 1))
        self.assertIn("INSERT INTO knowledge_chunks", cursor.calls[3][0])
        self.assertIn("%s::vector", cursor.calls[3][0])
        self.assertEqual(cursor.calls[4][1][:6],
                         ("refund-p1-c1", "refund", 1, 1, "片段 1", "[0.1,0.9]"))
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)

    def test_seventh_chunk_failure_propagates_out_of_transaction(self):
        cursor = FakeCursor([], fail_on_call=10)
        connection = FakeConnection(cursor)
        store = PostgresVectorStore(FakeEmbeddingService(), lambda: connection, dimensions=2)

        with self.assertRaisesRegex(RuntimeError, "第七个片段"):
            store.save_document("refund", "退款制度", "refund.pdf", 1, self.sample_chunks(7))

        self.assertEqual(len(cursor.calls), 10)
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

    def test_flagged_page_cannot_be_directly_published(self):
        store = PostgresVectorStore(FakeEmbeddingService(),
                                    lambda: self.fail("不能连接数据库"), dimensions=2)
        with self.assertRaisesRegex(ValueError, "页面不能直接发布"):
            store.save_document("refund", "退款制度", "refund.pdf", 1,
                                self.sample_chunks(1),
                                pages=[{"page": 1, "validation_status": "needs_review"}])

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
        self.assertIn("chunk.publication_status = 'published'", cursor.sql)
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

    def test_stage_document_saves_original_without_embedding(self):
        cursor = FakeCursor([], fetchone_results=[None])
        embedding = FakeEmbeddingService()
        store = PostgresVectorStore(embedding, lambda: FakeConnection(cursor), dimensions=2)
        result = {"document_id": "review", "title": "政策", "status": "partial",
                  "page_count": 1, "pages": [], "chunks": []}
        store.stage_document(result, "policy.pdf", b"%PDF-demo")
        self.assertIsNone(embedding.embedded_texts)
        self.assertIn("pg_advisory_xact_lock", cursor.calls[0][0])
        self.assertIn("INSERT INTO document_review_queue", cursor.calls[2][0])
        self.assertEqual(cursor.calls[2][1][3], b"%PDF-demo")

    def test_direct_publication_rejects_id_already_in_review_queue(self):
        cursor = FakeCursor([], fetchone_results=[{"id": "refund"}])
        connection = FakeConnection(cursor)
        store = PostgresVectorStore(FakeEmbeddingService(), lambda: connection, dimensions=2)

        with self.assertRaises(DocumentIdConflictError):
            store.save_document("refund", "退款制度", "refund.pdf", 1, self.sample_chunks(1))

        self.assertEqual(len(cursor.calls), 2)
        self.assertTrue(connection.rolled_back)

    def test_staging_rejects_id_already_published(self):
        cursor = FakeCursor([], fetchone_results=[{"id": "refund"}])
        connection = FakeConnection(cursor)
        store = PostgresVectorStore(FakeEmbeddingService(), lambda: connection, dimensions=2)

        with self.assertRaises(DocumentIdConflictError):
            store.stage_document({"document_id": "refund"}, "refund.pdf", b"%PDF-demo")

        self.assertEqual(len(cursor.calls), 2)
        self.assertTrue(connection.rolled_back)

    def test_approved_page_is_written_with_audit_and_published_filter(self):
        result = {"document_id": "review", "title": "退款制度", "status": "partial",
                  "page_count": 2,
                  "pages": [{"page": 1, "text": "退款三个工作日到账"},
                            {"page": 2, "text": ""}],
                  "chunks": [{"chunk_id": "review-p1-c0", "page": 1,
                              "chunk_index": 0, "content": "退款三个工作日到账",
                              "source_start": 0, "source_end": 9,
                              "heading_path": ["退款"], "validation_reasons": ["manual_review"]}]}
        staged = {"id": "review", "ingestion_result": result, "approved_pages": [],
                  "title": "退款制度", "source_filename": "policy.pdf",
                  "source_pdf": b"%PDF-demo"}
        cursor = FakeCursor([], fetchone_results=[staged, staged, None])
        connection = FakeConnection(cursor)
        store = PostgresVectorStore(FakeEmbeddingService(), lambda: connection, dimensions=2)
        published = store.publish_staged_pages("review", [1], "admin")
        self.assertEqual(published["published_pages"], [1])
        self.assertEqual(published["remaining_pages"], [2])
        self.assertTrue(connection.committed)
        self.assertTrue(any("INSERT INTO knowledge_chunks" in sql and "'published'" in sql
                            for sql, _ in cursor.calls))
        self.assertTrue(any("UPDATE document_review_queue" in sql for sql, _ in cursor.calls))


if __name__ == "__main__":
    unittest.main()
