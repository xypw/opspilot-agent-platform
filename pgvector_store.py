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
            raise ValueError(f"向量必须是 {self._dimensions} 维")
        try:
            normalized_values = [float(value) for value in values]
        except (TypeError, ValueError):
            raise ValueError("查询向量只能包含数字") from None
        return "[" + ",".join(str(value) for value in normalized_values) + "]"

    def save_document(
        self,
        document_id: str,
        title: str,
        source_filename: str,
        page_count: int,
        chunks: list[dict],
    ) -> int:
        """先生成全部向量，再以一个事务保存文档和所有片段。"""
        if any(not isinstance(value, str) or not value.strip()
               for value in (document_id, title, source_filename)):
            raise ValueError("文档编号、标题和来源文件名不能为空")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError("page_count 必须是正整数")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("至少需要一个知识片段")
        for chunk in chunks:
            if not isinstance(chunk, dict) or not isinstance(chunk.get("chunk_id"), str) \
                    or not chunk["chunk_id"].strip() or not isinstance(chunk.get("content"), str) \
                    or not chunk["content"].strip() or not isinstance(chunk.get("page"), int) \
                    or isinstance(chunk["page"], bool) or not 1 <= chunk["page"] <= page_count:
                raise ValueError("知识片段缺少有效编号、正文或页码")

        vectors = self.embedding_service.embed_documents([chunk["content"] for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("知识片段数量与向量数量不一致")
        vector_literals = [self._build_vector_literal(vector) for vector in vectors]

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO documents (id, title, source_filename, page_count)
                       VALUES (%s, %s, %s, %s)""",
                    (document_id, title, source_filename, page_count),
                )
                for index, (chunk, vector_literal) in enumerate(zip(chunks, vector_literals)):
                    cursor.execute(
                        """INSERT INTO knowledge_chunks
                           (id, document_id, page, chunk_index, content, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s::vector)""",
                        (chunk["chunk_id"], document_id, chunk["page"],
                         index, chunk["content"], vector_literal),
                    )
        return len(chunks)

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
