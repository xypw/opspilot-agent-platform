"""PostgreSQL/pgvector 语义检索仓库。"""

from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

from database import create_database_connection
from embedding_service import LocalEmbeddingService


ConnectionFactory = Callable[[], psycopg.Connection]


class PostgresVectorStore:
    """把问题向量交给 pgvector，并返回可引用的知识片段。"""

    def __init__(
        self,
        embedding_service: LocalEmbeddingService,
        connection_factory: ConnectionFactory = create_database_connection,
        dimensions: int = 512,
    ) -> None:
        if dimensions < 1:
            raise ValueError("dimensions 必须是正整数")
        self.embedding_service = embedding_service
        self._connection_factory = connection_factory
        self._dimensions = dimensions

    def _build_vector_literal(self, values: list[float]) -> str:
        """将 Python 数组转换成 pgvector 接受的文本，并检查维度。"""
        if len(values) != self._dimensions:
            raise ValueError(f"查询向量必须是 {self._dimensions} 维")
        try:
            normalized_values = [float(value) for value in values]
        except (TypeError, ValueError):
            raise ValueError("查询向量只能包含数字") from None
        return "[" + ",".join(str(value) for value in normalized_values) + "]"

    def search(self, query: str, limit: int = 3) -> list[dict]:
        """按余弦相似度从高到低返回片段，且不把完整向量暴露给 API。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit 必须是正整数")

        query_vector = self.embedding_service.embed_query(query.strip())
        vector_literal = self._build_vector_literal(query_vector)
        with self._connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT
                        chunk.id AS chunk_id,
                        document.title,
                        chunk.page,
                        chunk.content,
                        1 - (chunk.embedding <=> %s::vector) AS similarity
                    FROM knowledge_chunks AS chunk
                    JOIN documents AS document ON document.id = chunk.document_id
                    ORDER BY similarity DESC
                    LIMIT %s
                    """,
                    (vector_literal, limit),
                )
                rows = cursor.fetchall()

        return [
            {
                "chunk_id": row["chunk_id"],
                "title": row["title"],
                "page": row["page"],
                "content": row["content"],
                "score": float(row["similarity"]),
            }
            for row in rows
        ]
