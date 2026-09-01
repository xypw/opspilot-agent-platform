"""Embedding Service 离线测试；不下载模型，不访问外部 API。"""

import unittest

import numpy as np

from embedding_service import LocalEmbeddingService, MODEL_NAME, attach_embeddings


class FakeEmbeddingModel:
    def __init__(self):
        self.document_inputs = []
        self.query_inputs = []

    def passage_embed(self, texts):
        self.document_inputs.append(texts)
        return (np.array([float(index), 0.5]) for index, _ in enumerate(texts, start=1))

    def query_embed(self, query):
        self.query_inputs.append(query)
        return iter([np.array([0.8, 0.2])])


class EmbeddingServiceTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeEmbeddingModel()
        self.service = LocalEmbeddingService(model=self.model)

    def test_model_choice_is_fixed_for_reproducible_vectors(self):
        self.assertEqual(MODEL_NAME, "BAAI/bge-small-zh-v1.5")

    def test_documents_generator_becomes_json_friendly_list(self):
        vectors = self.service.embed_documents(["退款制度", "发票指南"])

        self.assertEqual(vectors, [[1.0, 0.5], [2.0, 0.5]])
        self.assertEqual(self.model.document_inputs, [["退款制度", "发票指南"]])
        self.assertIsInstance(vectors, list)
        self.assertIsInstance(vectors[0], list)

    def test_single_query_returns_one_vector(self):
        vector = self.service.embed_query("钱什么时候退回来")

        self.assertEqual(vector, [0.8, 0.2])
        self.assertEqual(self.model.query_inputs, ["钱什么时候退回来"])

    def test_empty_text_is_rejected_before_model(self):
        for texts in [[], [""], ["   "], [None]]:
            with self.subTest(texts=texts):
                with self.assertRaises(ValueError):
                    self.service.embed_documents(texts)
        self.assertEqual(self.model.document_inputs, [])

    def test_vectors_are_attached_to_matching_records(self):
        records = [
            {"chunk_id": "policy-p1-c0", "content": "退款制度"},
            {"chunk_id": "invoice-p1-c0", "content": "发票指南"},
        ]

        result = attach_embeddings(records, self.service)

        self.assertIs(result, records)
        self.assertEqual(result[0]["embedding"], [1.0, 0.5])
        self.assertEqual(result[1]["embedding"], [2.0, 0.5])

    def test_empty_records_do_not_load_model(self):
        self.assertEqual(attach_embeddings([], self.service), [])
        self.assertEqual(self.model.document_inputs, [])

    def test_missing_content_is_rejected_before_embedding(self):
        for records in [[{}], [{"content": ""}], [{"content": None}]]:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    attach_embeddings(records, self.service)
        self.assertEqual(self.model.document_inputs, [])

    def test_vector_count_must_match_record_count(self):
        class BrokenService:
            def embed_documents(self, texts):
                return [[0.1, 0.2]]

        with self.assertRaisesRegex(ValueError, "数量不一致"):
            attach_embeddings(
                [{"content": "第一块"}, {"content": "第二块"}], BrokenService()
            )


if __name__ == "__main__":
    unittest.main()
