"""真实 PG 双路召回/审核发布/增量迁移；每题使用独立 UUID schema。"""

import os
from pathlib import Path
import unittest
from unittest.mock import Mock
import uuid
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from database import load_database_url
from pgvector_store import PostgresVectorStore
from scripts.migrate_hybrid_search import migrate


class FixedEmbedding:
    """固定向量仅用于验证存储和执行链路，不衡量真实检索效果。"""
    def embed_query(self, query):
        return [1.0] + [0.0] * 511

    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 511 for text in texts]


@unittest.skipUnless(os.getenv("RUN_POSTGRES_INTEGRATION") == "true",
                     "显式设置 RUN_POSTGRES_INTEGRATION=true 后运行本机 PG 集成")
class PostgresHybridIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "opspilot_hybrid_it_" + uuid.uuid4().hex
        self.url = load_database_url()
        if urlsplit(self.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.skipTest("此集成测试只允许本机 PostgreSQL")
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup)
        root = Path(__file__).resolve().parents[1]
        with self.connection() as connection:
            connection.execute((root / "db/init.sql").read_text(encoding="utf-8"))
        self.store = PostgresVectorStore(FixedEmbedding(), self.connection)

    def connection(self):
        return psycopg.connect(self.url, options=f"-c search_path={self.schema},public")

    def cleanup(self):
        # 只删除当前测试随机创建的 schema；名字由本类生成，不接受外部参数。
        assert self.schema.startswith("opspilot_hybrid_it_") and len(self.schema) == 51
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def save(self):
        self.store.save_document("refund", "退款规则", "fixture.pdf", 2, [
            {"chunk_id": "refund-p1-c0", "page": 1, "content": "退款审核通过后三个工作日到账。",
             "source_start": 0, "source_end": 18, "heading_path": ["到账"]},
            {"chunk_id": "refund-p2-c0", "page": 2, "content": "E_REFUND_TIMEOUT 请联系售后查询。",
             "source_start": 0, "source_end": 29, "heading_path": ["异常"]},
        ])

    def test_persistent_candidates_and_fake_reranker_survive_store_recreation(self):
        self.save()
        replacement = PostgresVectorStore(FixedEmbedding(), self.connection)
        reranker = Mock()
        reranker.score.side_effect = lambda query, docs: [0.99 if "E_REFUND_TIMEOUT" in d else 0.1 for d in docs]
        results = replacement.search_hybrid("退款", 1, reranker=reranker)
        self.assertEqual(results[0]["chunk_id"], "refund-p2-c0")
        self.assertEqual(results[0]["page"], 2)
        self.assertEqual(results[0]["source_start"], 0)
        self.assertEqual(results[0]["heading_path"], ["异常"])
        self.assertEqual(results[0]["score"], 0.99)
        reranker.score.assert_called_once()

    def test_both_retrieval_lanes_exclude_unpublished_document_and_chunk(self):
        self.save()
        with self.connection() as connection:
            connection.execute("UPDATE knowledge_chunks SET publication_status='pending_review'")
        self.assertEqual(self.store.search_hybrid("退款 E_REFUND_TIMEOUT"), [])
        with self.connection() as connection:
            connection.execute("UPDATE knowledge_chunks SET publication_status='published'")
            # documents 的正式状态只有 published/partial_published；删掉文档前只删除本测试数据。
            connection.execute("DELETE FROM documents WHERE id='refund'")
        self.assertEqual(self.store.search_hybrid("退款"), [])

    def test_staging_has_no_index_until_only_approved_page_is_published(self):
        result = {"document_id": "review", "title": "退款规则", "status": "partial", "page_count": 2,
                  "pages": [{"page": 1, "text": "退款三个工作日到账"},
                            {"page": 2, "text": "退款需人工核实"}],
                  "chunks": [{"chunk_id": "review-p1-c0", "page": 1, "chunk_index": 0,
                              "content": "退款三个工作日到账", "source_start": 0, "source_end": 10},
                             {"chunk_id": "review-p2-c0", "page": 2, "chunk_index": 1,
                              "content": "退款需人工核实", "source_start": 0, "source_end": 8}]}
        self.store.stage_document(result, "fixture.pdf", b"%PDF-fictional")
        self.assertEqual(self.store.search_hybrid("退款"), [])
        published = self.store.publish_staged_pages("review", [1], "IT-reviewer")
        self.assertEqual(published["remaining_pages"], [2])
        self.assertEqual([r["page"] for r in self.store.search_hybrid("退款")], [1])
        with self.connection() as connection:
            count, terms = connection.execute(
                "SELECT count(*), min(cardinality(lexical_terms)) FROM knowledge_chunks").fetchone()
            self.assertEqual(count, 1)
            self.assertGreater(terms, 0)
        with self.assertRaises(ValueError):
            self.store.publish_staged_pages("review", [1], "IT-reviewer")

    def test_legacy_backfill_is_batched_repeatable_and_preserves_embedding(self):
        self.save()
        with self.connection() as connection:
            before = connection.execute("SELECT id, embedding::text FROM knowledge_chunks ORDER BY id").fetchall()
            connection.execute("ALTER TABLE knowledge_chunks DROP COLUMN lexical_terms CASCADE")
        first = migrate(self.connection, batch_size=1)
        second = migrate(self.connection, batch_size=1)
        self.assertEqual(first, {"updated_chunks": 2, "external_model_calls": 0})
        self.assertEqual(second["updated_chunks"], 0)
        with self.connection() as connection:
            after = connection.execute("SELECT id, embedding::text FROM knowledge_chunks ORDER BY id").fetchall()
        self.assertEqual(before, after)
        self.assertEqual(len(self.store.search_hybrid("退款")), 2)


if __name__ == "__main__":
    unittest.main()
