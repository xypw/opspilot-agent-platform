"""PDF 入库流水线测试：页码、切块和 OCR 待处理状态。"""

from io import BytesIO
import unittest
from unittest.mock import patch

from document_ingestion import ingest_pdf


class DocumentIngestionTests(unittest.TestCase):
    def test_text_pages_become_citable_chunks(self):
        pages = [
            {"page": 1, "text": "ABCDEFGHIJKL", "needs_ocr": False},
            {"page": 2, "text": "退款三个工作日到账", "needs_ocr": False},
        ]
        with patch("document_ingestion.parse_pdf", return_value=pages):
            result = ingest_pdf(
                BytesIO(), "refund-policy", "售后与退款制度",
                chunk_size=6, overlap=2,
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["chunk_count"], 5)
        self.assertEqual(result["ocr_required_pages"], [])
        self.assertEqual(result["chunks"][0]["chunk_id"], "refund-policy-p1-c0")
        self.assertEqual(result["chunks"][-1]["page"], 2)
        self.assertEqual(result["chunks"][-1]["extraction_method"], "text")
        self.assertIsNone(result["chunks"][-1]["ocr_confidence"])

    def test_image_page_is_reported_instead_of_silently_lost(self):
        pages = [
            {"page": 1, "text": "可以直接检索", "needs_ocr": False},
            {"page": 2, "text": "", "needs_ocr": True},
        ]
        with patch("document_ingestion.parse_pdf", return_value=pages):
            result = ingest_pdf(BytesIO(), "support-guide", "客服指南")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["ocr_required_pages"], [2])
        self.assertEqual(result["chunk_count"], 1)
        self.assertEqual(result["chunks"][0]["page"], 1)


if __name__ == "__main__":
    unittest.main()
