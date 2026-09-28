"""PDF 解析测试：真实 PDF 结构、页码保留和错误文件。"""

from io import BytesIO
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from document_parser import PARSER_VERSION, parse_pdf


class DocumentParserTests(unittest.TestCase):
    def test_real_blank_pdf_is_identified_as_needing_ocr(self):
        pdf_bytes = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf_bytes)
        pdf_bytes.seek(0)

        pages = parse_pdf(pdf_bytes)
        self.assertEqual([page["validation_reasons"] for page in pages],
                         [["needs_ocr"], ["needs_ocr"]])
        self.assertTrue(all(page["validation_status"] == "needs_review" for page in pages))

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
            pages = parse_pdf(BytesIO())
            self.assertEqual([page["text"] for page in pages], ["第一页制度", "第二页流程"])
            self.assertTrue(all(page["parser_version"] == PARSER_VERSION for page in pages))
            self.assertEqual(pages[0]["validation_reasons"], ["unusually_short_text"])

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
            pages = parse_pdf(BytesIO())
            self.assertEqual([page["needs_ocr"] for page in pages], [False, True])
            self.assertIn("needs_ocr", pages[1]["validation_reasons"])

    def test_garbled_text_is_flagged_before_publication(self):
        class FakePage:
            def extract_text(self):
                return "退货规则����相关内容需要人工核对"

        class FakeReader:
            is_encrypted = False
            pages = [FakePage()]

        with patch("document_parser.PdfReader", return_value=FakeReader()):
            pages = parse_pdf(BytesIO())

        self.assertEqual(pages[0]["validation_status"], "needs_review")
        self.assertIn("possible_garbled_text", pages[0]["validation_reasons"])

    def test_invalid_pdf_has_safe_error(self):
        with self.assertRaisesRegex(ValueError, "PDF 文件无法读取"):
            parse_pdf(BytesIO(b"not a pdf"))


if __name__ == "__main__":
    unittest.main()
