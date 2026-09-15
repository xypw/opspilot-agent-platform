"""本地接口测试，不连接模型服务。"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from document_parser import PDFNeedsOCRError
from knowledge_base import DuplicateDocumentError, KnowledgeStoreUnavailableError
from vector_store import InMemoryVectorStore


class FakeEmbeddingService:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "退款" in text else [0.0, 1.0]
            for text in texts
        ]

    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0] if "退款" in query else [0.0, 1.0]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        backend_patch = patch("knowledge_base.KNOWLEDGE_STORE_BACKEND", "memory")
        backend_patch.start()
        self.addCleanup(backend_patch.stop)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "opspilot"})

    def test_interactive_docs(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("swagger-ui", response.text)

    def test_health_is_in_openapi(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("get", response.json()["paths"]["/health"])

    def test_unknown_path_returns_404(self):
        response = self.client.get("/not-a-route")
        self.assertEqual(response.status_code, 404)

    def test_ticket_route_passes_id_to_query(self):
        sample = {"id": "T-1003", "status": "open", "priority": "medium"}
        # 这里临时替换查询函数，只测试老师提供的 HTTP 接口层。
        with patch("main.query_ticket", return_value=sample) as query:
            response = self.client.get("/tickets/T-1003")
        query.assert_called_once_with("T-1003")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), sample)

    def test_ticket_not_found_returns_404(self):
        with patch("main.query_ticket", return_value=None):
            response = self.client.get("/tickets/T-9999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "工单不存在"})

    def test_real_query_finds_first_ticket(self):
        # 不替换查询函数，验证路由和真实业务逻辑的连接。
        response = self.client.get("/tickets/T-1001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"id": "T-1001", "status": "open", "priority": "high"}
        )

    def test_real_query_finds_last_ticket(self):
        response = self.client.get("/tickets/T-1003")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"id": "T-1003", "status": "open", "priority": "medium"}
        )

    def test_real_query_missing_ticket_returns_404(self):
        response = self.client.get("/tickets/T-9999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "工单不存在"})

    def test_real_order_query_uses_student_route_logic(self):
        response = self.client.get("/orders/O-2001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "id": "O-2001",
            "status": "delivered",
            "product": "机械键盘",
            "delivered_at": "2026-08-31",
            "amount_cents": 39900,
        })

    def test_missing_order_returns_404(self):
        response = self.client.get("/orders/O-9999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "订单不存在"})

    def test_knowledge_search_reads_pydantic_request_fields(self):
        with patch("main.search_knowledge_base", return_value=[]) as search:
            response = self.client.post(
                "/knowledge/search",
                json={"query": "退款多久到账", "limit": 2},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        search.assert_called_once_with("退款多久到账", 2)

    def test_knowledge_search_returns_citable_result(self):
        response = self.client.post(
            "/knowledge/search",
            json={"query": "退款多久到账", "limit": 1},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()[0]
        self.assertEqual(result["title"], "售后与退款制度")
        self.assertEqual(result["page"], 2)
        self.assertIn("三个工作日", result["content"])

    def test_knowledge_search_does_not_return_timing_as_refund_steps(self):
        response = self.client.post(
            "/knowledge/search",
            json={"query": "我应该怎么退款", "limit": 3},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_knowledge_search_database_failure_returns_503(self):
        with patch("main.search_knowledge_base", side_effect=KnowledgeStoreUnavailableError()):
            response = self.client.post(
                "/knowledge/search",
                json={"query": "退款多久到账", "limit": 3},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "知识库暂时无法检索文档"})

    def test_knowledge_search_rejects_invalid_limit_before_function(self):
        with patch("main.search_knowledge_base") as search:
            response = self.client.post(
                "/knowledge/search",
                json={"query": "退款", "limit": 6},
            )
        self.assertEqual(response.status_code, 422)
        search.assert_not_called()

    def test_pdf_upload_connects_http_to_ingestion_pipeline(self):
        result = {
            "document_id": "refund-policy",
            "title": "售后与退款制度",
            "status": "ready",
            "page_count": 1,
            "chunk_count": 1,
            "ocr_required_pages": [],
            "chunks": [{
                "chunk_id": "refund-policy-p1-c0",
                "title": "售后与退款制度",
                "page": 1,
                "content": "退款将在三个工作日内到账。",
                "extraction_method": "text",
                "ocr_confidence": None,
            }],
        }
        with (
            patch("main.ingest_pdf", return_value=result) as ingest,
            patch("main.index_uploaded_document") as index_document,
        ):
            response = self.client.post(
                "/documents/upload",
                data={"document_id": "refund-policy", "title": "售后与退款制度"},
                files={"file": ("policy.pdf", b"fake-pdf", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        source = ingest.call_args.args[0]
        self.assertEqual(source.read(), b"fake-pdf")
        self.assertEqual(ingest.call_args.kwargs, {
            "document_id": "refund-policy", "title": "售后与退款制度",
        })
        index_document.assert_called_once_with(result, "policy.pdf")

    def test_duplicate_document_id_returns_409_without_hiding_conflict(self):
        result = {
            "document_id": "refund-policy", "title": "售后与退款制度", "status": "ready",
            "page_count": 1, "chunk_count": 1, "ocr_required_pages": [],
            "chunks": [{"chunk_id": "refund-policy-p1-c0", "title": "售后与退款制度",
                        "page": 1, "content": "退款说明", "extraction_method": "text",
                        "ocr_confidence": None}],
        }
        with (
            patch("main.ingest_pdf", return_value=result),
            patch("main.index_uploaded_document", side_effect=DuplicateDocumentError()),
        ):
            response = self.client.post(
                "/documents/upload",
                data={"document_id": "refund-policy", "title": "售后与退款制度"},
                files={"file": ("policy.pdf", b"fake-pdf", "application/pdf")},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "文档或片段编号已存在"})

    def test_storage_failure_returns_503_not_invalid_pdf(self):
        result = {
            "document_id": "refund-policy", "title": "售后与退款制度", "status": "ready",
            "page_count": 1, "chunk_count": 1, "ocr_required_pages": [],
            "chunks": [{"chunk_id": "refund-policy-p1-c0", "title": "售后与退款制度",
                        "page": 1, "content": "退款说明", "extraction_method": "text",
                        "ocr_confidence": None}],
        }
        with (
            patch("main.ingest_pdf", return_value=result),
            patch("main.index_uploaded_document", side_effect=KnowledgeStoreUnavailableError()),
        ):
            response = self.client.post(
                "/documents/upload",
                data={"document_id": "refund-policy", "title": "售后与退款制度"},
                files={"file": ("policy.pdf", b"fake-pdf", "application/pdf")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "知识库暂时无法保存文档"})

    def test_upload_then_semantic_search_uses_indexed_chunks(self):
        vector_store = InMemoryVectorStore(FakeEmbeddingService())
        ingestion_result = {
            "document_id": "company-guide",
            "title": "企业服务指南",
            "status": "ready",
            "page_count": 1,
            "chunk_count": 2,
            "ocr_required_pages": [],
            "chunks": [
                {
                    "chunk_id": "company-guide-p1-c0",
                    "title": "企业服务指南",
                    "page": 1,
                    "content": "退款将在三个工作日内到账。",
                    "extraction_method": "text",
                    "ocr_confidence": None,
                },
                {
                    "chunk_id": "company-guide-p1-c1",
                    "title": "企业服务指南",
                    "page": 1,
                    "content": "电子发票会发送到指定邮箱。",
                    "extraction_method": "text",
                    "ocr_confidence": None,
                },
            ],
        }

        with (
            patch("main.ingest_pdf", return_value=ingestion_result),
            patch("knowledge_base.VECTOR_STORE", vector_store),
            # 测试混合检索接口本身，不让本地是否配置云端 Key 改变测试结果。
            patch("knowledge_base.RERANKER", None),
        ):
            upload_response = self.client.post(
                "/documents/upload",
                data={"document_id": "company-guide", "title": "企业服务指南"},
                files={"file": ("guide.pdf", b"fake-pdf", "application/pdf")},
            )
            search_response = self.client.post(
                "/knowledge/search",
                json={"query": "退款多久到账", "limit": 1},
            )

        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(search_response.status_code, 200)
        self.assertEqual(search_response.json()[0]["chunk_id"], "company-guide-p1-c0")
        self.assertAlmostEqual(search_response.json()[0]["score"], 1 / 61 + 1 / 61)

    def test_upload_rejects_non_pdf_before_ingestion(self):
        with patch("main.ingest_pdf") as ingest:
            response = self.client.post(
                "/documents/upload",
                data={"document_id": "notes", "title": "说明"},
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
        self.assertEqual(response.status_code, 415)
        ingest.assert_not_called()

    def test_scan_only_pdf_reports_ocr_requirement(self):
        with patch("main.ingest_pdf", side_effect=PDFNeedsOCRError("需要先进行 OCR")):
            response = self.client.post(
                "/documents/upload",
                data={"document_id": "scan", "title": "扫描件"},
                files={"file": ("scan.pdf", b"fake-pdf", "application/pdf")},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"detail": "需要先进行 OCR"})


if __name__ == "__main__":
    unittest.main()
