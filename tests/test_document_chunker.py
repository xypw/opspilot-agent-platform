"""文档切块测试：重叠、文档结尾和错误参数。"""

import unittest

from document_chunker import build_chunk_records, split_text, split_structured_text


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
            "source_start": 0,
            "source_end": 6,
            "heading_path": [],
        })
        self.assertEqual(records[1]["chunk_id"], "refund-policy-p2-c1")
        self.assertEqual(records[1]["content"], "EFGHIJ")

    def test_invalid_source_metadata_is_rejected(self):
        for document_id, title, page in [("", "标题", 1), ("doc", " ", 1), ("doc", "标题", 0)]:
            with self.subTest(document_id=document_id, title=title, page=page):
                with self.assertRaises(ValueError):
                    build_chunk_records(document_id, title, page, "正文")

    def test_structure_boundaries_keep_exact_source_offsets(self):
        text = "# 售后政策\n申请条件：已签收七天内。\n\n- 不得退货的商品另列。\n"
        parts = split_structured_text(text, chunk_size=40, overlap=5)
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(text[p["source_start"]:p["source_end"]] == p["content"]
                            for p in parts))
        self.assertEqual(parts[0]["heading_path"], ["# 售后政策"])

    def test_separate_policy_rules_without_splitting_condition_from_outcome(self):
        text = "退款到账时间\n普通退款：审核通过后 3 个工作日到账。\n紧急退款：标记为紧急且审核通过后 3 小时到账。"
        parts = split_structured_text(text)
        ordinary = next(part for part in parts if "普通退款：" in part["content"])
        urgent = next(part for part in parts if "紧急退款：" in part["content"])
        self.assertNotEqual(ordinary, urgent)
        self.assertNotIn("紧急退款", ordinary["content"])
        self.assertIn("标记为紧急且审核通过后 3 小时到账", urgent["content"])
        self.assertTrue(all(text[part["source_start"]:part["source_end"]] == part["content"]
                            for part in parts))

    def test_plain_heading_after_finished_rule_starts_new_chunk(self):
        text = "紧急退款：审核通过后 3 小时到账。\n高优先级工单\n高优先级工单将在 20 分钟内响应。"
        parts = split_structured_text(text)
        urgent = next(part for part in parts if "紧急退款：" in part["content"])
        self.assertNotIn("高优先级工单", urgent["content"])
        self.assertTrue(any("高优先级工单" in part["content"] for part in parts))


if __name__ == "__main__":
    unittest.main()
