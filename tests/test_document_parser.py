"""PDF 解析测试：真实 PDF 结构、页码保留和错误文件。"""

from io import BytesIO
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from document_parser import PDFNeedsOCRError, parse_pdf


class DocumentParserTests(unittest.TestCase):
    def test_real_blank_pdf_is_identified_as_needing_ocr(self):
        pdf_bytes = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf_bytes)
        pdf_bytes.seek(0)

        with self.assertRaisesRegex(PDFNeedsOCRError, "需要先进行 OCR"):
            parse_pdf(pdf_bytes)

    def test_extracted_text_is_kept_with_its_page(self):
        class FakePage:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class FakeReader:
            is_encrypted = False
            pages = [FakePage(" 第一页制度 "), FakePage("第二页流程")]

        with patch("document_parser.PdfReader", return_value=FakeReader()):
            self.assertEqual(parse_pdf(BytesIO()), [
                {"page": 1, "text": "第一页制度", "needs_ocr": False},
                {"page": 2, "text": "第二页流程", "needs_ocr": False},
            ])

    def test_mixed_pdf_marks_only_empty_page_for_ocr(self):
        class FakePage:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        class FakeReader:
            is_encrypted = False
            pages = [FakePage("可搜索文本"), FakePage(None)]

        with patch("document_parser.PdfReader", return_value=FakeReader()):
            self.assertEqual(parse_pdf(BytesIO()), [
                {"page": 1, "text": "可搜索文本", "needs_ocr": False},
                {"page": 2, "text": "", "needs_ocr": True},
            ])

    def test_invalid_pdf_has_safe_error(self):
        with self.assertRaisesRegex(ValueError, "PDF 文件无法读取"):
            parse_pdf(BytesIO(b"not a pdf"))


if __name__ == "__main__":
    unittest.main()
