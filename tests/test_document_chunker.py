"""文档切块测试：重叠、文档结尾和错误参数。"""

import unittest

from document_chunker import build_chunk_records, split_text


class DocumentChunkerTests(unittest.TestCase):
    def test_chunks_keep_expected_overlap(self):
        chunks = split_text("ABCDEFGHIJKL", chunk_size=6, overlap=2)

        self.assertEqual(chunks, ["ABCDEF", "EFGHIJ", "IJKL"])
        self.assertEqual(chunks[0][-2:], chunks[1][:2])
        self.assertEqual(chunks[1][-2:], chunks[2][:2])

    def test_short_text_stays_in_one_chunk(self):
        self.assertEqual(split_text("退款规则", chunk_size=100, overlap=20), ["退款规则"])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(split_text("", chunk_size=100, overlap=20), [])

    def test_invalid_sizes_fail_before_loop(self):
        for chunk_size, overlap in [(0, 0), (100, -1), (100, 100), (100, 101)]:
            with self.subTest(chunk_size=chunk_size, overlap=overlap):
                with self.assertRaises(ValueError):
                    split_text("测试文本", chunk_size=chunk_size, overlap=overlap)

    def test_records_keep_source_metadata(self):
        records = build_chunk_records(
            "refund-policy", "售后与退款制度", 2, "ABCDEFGHIJKL",
            chunk_size=6, overlap=2,
        )

        self.assertEqual(records[0], {
            "chunk_id": "refund-policy-p2-c0",
            "title": "售后与退款制度",
            "page": 2,
            "content": "ABCDEF",
        })
        self.assertEqual(records[1]["chunk_id"], "refund-policy-p2-c1")
        self.assertEqual(records[1]["content"], "EFGHIJ")

    def test_invalid_source_metadata_is_rejected(self):
        for document_id, title, page in [("", "标题", 1), ("doc", " ", 1), ("doc", "标题", 0)]:
            with self.subTest(document_id=document_id, title=title, page=page):
                with self.assertRaises(ValueError):
                    build_chunk_records(document_id, title, page, "正文")


if __name__ == "__main__":
    unittest.main()
