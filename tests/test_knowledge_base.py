"""关键词检索基线测试；后续替换成混合检索时保留同一返回契约。"""

import unittest
from unittest.mock import patch

from knowledge_base import index_knowledge_chunks, search_knowledge_base
from tool_executor import execute_tool
from vector_store import InMemoryVectorStore


class FakeEmbeddingService:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "退款" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0]


class KnowledgeBaseTests(unittest.TestCase):
    def test_refund_question_returns_citable_source(self):
        results = search_knowledge_base("退款多久到账", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "售后与退款制度")
        self.assertEqual(results[0]["page"], 2)
        self.assertIn("三个工作日", results[0]["content"])
        self.assertGreater(results[0]["score"], 0)

    def test_irrelevant_question_returns_no_evidence(self):
        self.assertEqual(search_knowledge_base("食堂菜单"), [])

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

        with patch("knowledge_base.VECTOR_STORE", store):
            index_knowledge_chunks(chunks)
            results = execute_tool(
                "search_knowledge_base",
                '{"query":"退款多久到账","limit":1}',
            )

        self.assertEqual(results[0]["chunk_id"], "uploaded-p1-c0")
        self.assertEqual(results[0]["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
