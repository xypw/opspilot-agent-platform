"""内存向量库：演示 RAG 的索引阶段和检索阶段。"""

from embedding_service import LocalEmbeddingService, attach_embeddings
from vector_search import semantic_search


class InMemoryVectorStore:
    """保存已经计算好的文档向量，并支持语义检索。"""

    def __init__(self, embedding_service: LocalEmbeddingService):
        self.embedding_service = embedding_service
        self.records: list[dict] = []

    def add(self, records: list[dict]) -> None:
        """索引阶段：文档向量只在添加时计算一次。"""
        copied_records = [dict(record) for record in records]
        embedded_records = attach_embeddings(copied_records, self.embedding_service)
        self.records.extend(embedded_records)

    def search(self, query: str, limit: int = 3) -> list[dict]:
        """检索阶段：只计算问题向量，并复用已经保存的文档向量。"""
        return semantic_search(
            query=query,
            records=self.records,
            service=self.embedding_service,
            limit=limit,
        )
