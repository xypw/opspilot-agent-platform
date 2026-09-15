"""关键词检索基线测试；后续替换成混合检索时保留同一返回契约。"""

import unittest
from unittest.mock import patch
from unittest.mock import Mock

import psycopg

from knowledge_base import (
    DuplicateDocumentError,
    KnowledgeStoreUnavailableError,
    index_knowledge_chunks,
    index_uploaded_document,
    load_min_semantic_similarity,
    search_knowledge_base,
)
from tool_executor import execute_tool
from vector_store import InMemoryVectorStore


class FakeEmbeddingService:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "退款" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return [0.0, 1.0] if "食堂" in query else [1.0, 0.0]


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        backend_patch = patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "memory")
        backend_patch.start()
        self.addCleanup(backend_patch.stop)
        threshold_patch = patch("knowledge_base.MIN_SEMANTIC_SIMILARITY", 0.6)
        threshold_patch.start()
        self.addCleanup(threshold_patch.stop)

    def test_postgres_upload_passes_metadata_and_chunks_to_repository(self):
        chunks = [{"chunk_id": "refund-p1-c0", "page": 1, "content": "退款说明"}]
        result = {"document_id": "refund", "title": "退款制度", "page_count": 1,
                  "chunks": chunks}
        store = Mock()

        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            index_uploaded_document(result, "refund.pdf")

        store.save_document.assert_called_once_with(
            document_id="refund", title="退款制度", source_filename="refund.pdf",
            page_count=1, chunks=chunks,
        )

    def test_postgres_unique_violation_becomes_duplicate_document_error(self):
        store = Mock()
        store.save_document.side_effect = psycopg.errors.UniqueViolation("重复编号")
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
            self.assertRaises(DuplicateDocumentError),
        ):
            index_uploaded_document(
                {"document_id": "refund", "title": "退款制度", "page_count": 1,
                 "chunks": [{"chunk_id": "refund-p1-c0", "page": 1, "content": "退款说明"}]},
                "refund.pdf",
            )

    def test_postgres_connection_failure_is_not_reported_as_pdf_error(self):
        store = Mock()
        store.save_document.side_effect = psycopg.OperationalError("连接失败")
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
            self.assertRaises(KnowledgeStoreUnavailableError),
        ):
            index_uploaded_document(
                {"document_id": "refund", "title": "退款制度", "page_count": 1,
                 "chunks": [{"chunk_id": "refund-p1-c0", "page": 1, "content": "退款说明"}]},
                "refund.pdf",
            )

    def test_postgres_search_filters_low_score_without_reading_demo_chunks(self):
        store = Mock()
        store.search.return_value = [
            {"chunk_id": "stored", "title": "已上传退款制度", "page": 1,
             "content": "退款将在三个工作日内到账。", "score": 0.91},
            {"chunk_id": "weak", "title": "弱相关资料", "page": 2,
             "content": "其他内容。", "score": 0.21},
        ]
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            results = search_knowledge_base("退款多久到账", limit=2)

        self.assertEqual([item["chunk_id"] for item in results], ["stored"])
        store.search.assert_called_once_with("退款多久到账", 6)

    def test_postgres_filter_can_use_a_later_procedure_candidate(self):
        store = Mock()
        store.search.return_value = [
            {"chunk_id": "timing", "title": "到账", "page": 1,
             "content": "退款审核通过后三个工作日到账。", "score": 0.93},
            {"chunk_id": "steps", "title": "申请步骤", "page": 2,
             "content": "进入订单详情页，点击申请退款并提交。", "score": 0.82},
        ]
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            results = search_knowledge_base("我应该怎么退款", limit=1)

        self.assertEqual([item["chunk_id"] for item in results], ["steps"])
        store.search.assert_called_once_with("我应该怎么退款", 3)

    def test_postgres_can_return_offline_counter_instruction(self):
        store = Mock()
        store.search.return_value = [{
            "chunk_id": "counter", "title": "线下售后说明", "page": 1,
            "content": "携带订单号和商品至售后服务台办理退款。", "score": 0.81,
        }]
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            results = search_knowledge_base("如何申请退款", limit=1)

        self.assertEqual([item["chunk_id"] for item in results], ["counter"])

    def test_postgres_empty_result_does_not_fall_back_to_demo_chunks(self):
        store = Mock()
        store.search.return_value = []
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            self.assertEqual(search_knowledge_base("退款多久到账"), [])

    def test_postgres_timing_chunk_cannot_answer_how_to_question(self):
        store = Mock()
        store.search.return_value = [{
            "chunk_id": "stored", "title": "已上传退款制度", "page": 1,
            "content": "退款审核通过后三个工作日到账。", "score": 0.91,
        }]
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            self.assertEqual(search_knowledge_base("我应该怎么退款"), [])

    def test_postgres_high_score_status_cannot_answer_how_to_question(self):
        store = Mock()
        store.search.return_value = [{
            "chunk_id": "status", "title": "退款状态说明", "page": 1,
            "content": "客户提交退款申请后，通常三个工作日到账。", "score": 0.888,
        }]
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
        ):
            self.assertEqual(search_knowledge_base("我应该怎么退款"), [])

    def test_postgres_search_failure_is_not_no_evidence(self):
        store = Mock()
        store.search.side_effect = psycopg.OperationalError("连接失败")
        with (
            patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "postgres"),
            patch("knowledge_base.POSTGRES_VECTOR_STORE", store),
            self.assertRaises(KnowledgeStoreUnavailableError),
        ):
            search_knowledge_base("退款多久到账")

    def test_refund_question_returns_citable_source(self):
        results = search_knowledge_base("退款多久到账", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "售后与退款制度")
        self.assertEqual(results[0]["page"], 2)
        self.assertIn("三个工作日", results[0]["content"])
        self.assertGreater(results[0]["score"], 0)

    def test_irrelevant_question_returns_no_evidence(self):
        self.assertEqual(search_knowledge_base("食堂菜单"), [])

    def test_refund_how_to_is_not_answered_by_refund_arrival_time(self):
        self.assertEqual(search_knowledge_base("我应该怎么退款"), [])

    def test_uploaded_refund_procedure_can_answer_how_to(self):
        store = InMemoryVectorStore(FakeEmbeddingService())
        with (
            patch("knowledge_base.VECTOR_STORE", store),
            patch("knowledge_base.RERANKER", None),
        ):
            index_knowledge_chunks([{
                "chunk_id": "refund-steps-p1-c0",
                "title": "退款申请步骤",
                "page": 1,
                "content": "打开订单详情页，点击申请退款，填写原因后提交。",
            }])
            results = search_knowledge_base("我应该怎么退款", limit=1)

        self.assertEqual([item["chunk_id"] for item in results], ["refund-steps-p1-c0"])

    def test_limit_controls_result_count(self):
        self.assertLessEqual(len(search_knowledge_base("工单订单发票", limit=2)), 2)

    def test_uploaded_chunks_are_available_to_agent_tool(self):
        store = InMemoryVectorStore(FakeEmbeddingService())
        chunks = [
            {
                "chunk_id": "uploaded-p1-c0",
                "title": "上传的退款规定",
                "page": 1,
                "content": "退款将在一个工作日内到账。",
            }
        ]

        # 显式关闭外部 Reranker，让单元测试不受开发者本地 .env 配置影响。
        with (
            patch("knowledge_base.VECTOR_STORE", store),
            patch("knowledge_base.RERANKER", None),
        ):
            index_knowledge_chunks(chunks)
            results = execute_tool(
                "search_knowledge_base",
                '{"query":"退款多久到账","limit":1}',
            )

        self.assertEqual(results[0]["chunk_id"], "uploaded-p1-c0")
        self.assertAlmostEqual(results[0]["score"], 1 / 61 + 1 / 61)

    def test_uploaded_irrelevant_question_returns_empty_tool_result(self):
        store = InMemoryVectorStore(FakeEmbeddingService())
        with (
            patch("knowledge_base.VECTOR_STORE", store),
            patch("knowledge_base.RERANKER", None),
        ):
            index_knowledge_chunks([{
                "chunk_id": "refund-p1-c0",
                "title": "退款规定",
                "page": 1,
                "content": "退款将在一个工作日内到账。",
            }])
            results = execute_tool(
                "search_knowledge_base",
                '{"query":"食堂菜单","limit":1}',
            )
        self.assertEqual(results, [])

    def test_min_similarity_configuration_rejects_invalid_values(self):
        with patch.dict("os.environ", {"KNOWLEDGE_MIN_COSINE_SIMILARITY": "0.7"}):
            self.assertEqual(load_min_semantic_similarity(), 0.7)
        for invalid in ("nan", "1.2", "abc"):
            with (
                self.subTest(invalid=invalid),
                patch.dict("os.environ", {"KNOWLEDGE_MIN_COSINE_SIMILARITY": invalid}),
                self.assertRaises(ValueError),
            ):
                load_min_semantic_similarity()


if __name__ == "__main__":
    unittest.main()
