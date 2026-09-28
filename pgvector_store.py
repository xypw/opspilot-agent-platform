"""PostgreSQL/pgvector 语义检索仓库。"""

from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from database import create_database_connection
from embedding_service import LocalEmbeddingService


ConnectionFactory = Callable[[], psycopg.Connection]


class DocumentIdConflictError(ValueError):
    """同一编号不能同时占据正式索引与待审核区。"""


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
        source_pdf: bytes | None = None,
        pages: list[dict] | None = None,
    ) -> int:
        """先生成全部向量，再以一个事务保存文档和所有片段。"""
        if any(not isinstance(value, str) or not value.strip()
               for value in (document_id, title, source_filename)):
            raise ValueError("文档编号、标题和来源文件名不能为空")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError("page_count 必须是正整数")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("至少需要一个知识片段")
        if any(page.get("validation_status", "validated") != "validated"
               for page in pages or []):
            raise ValueError("未通过校验的页面不能直接发布")
        for chunk in chunks:
            if not isinstance(chunk, dict) or not isinstance(chunk.get("chunk_id"), str) \
                    or not chunk["chunk_id"].strip() or not isinstance(chunk.get("content"), str) \
                    or not chunk["content"].strip() or not isinstance(chunk.get("page"), int) \
                    or isinstance(chunk["page"], bool) or not 1 <= chunk["page"] <= page_count:
                raise ValueError("知识片段缺少有效编号、正文或页码")
            if chunk.get("validation_status", "validated") != "validated":
                raise ValueError("未通过校验的片段不能直接发布")

        vectors = self.embedding_service.embed_documents([chunk["content"] for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("知识片段数量与向量数量不一致")
        vector_literals = [self._build_vector_literal(vector) for vector in vectors]

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                # 两条上传路径共用同一文档编号空间，事务锁避免跨表竞态。
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (document_id,))
                cursor.execute("SELECT id FROM document_review_queue WHERE id = %s", (document_id,))
                if cursor.fetchone() is not None:
                    raise DocumentIdConflictError("文档编号已存在于待审核区")
                cursor.execute(
                    """INSERT INTO documents
                       (id, title, source_filename, page_count, source_pdf, page_audit,
                        parser_version, publication_status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'published')""",
                    (document_id, title, source_filename, page_count, source_pdf,
                     Jsonb(pages or []), chunks[0].get("parser_version", "legacy")),
                )
                for index, (chunk, vector_literal) in enumerate(zip(chunks, vector_literals)):
                    cursor.execute(
                        """INSERT INTO knowledge_chunks
                           (id, document_id, page, chunk_index, content, embedding,
                            source_start, source_end, heading_path, parser_version,
                            validation_status, validation_reasons, publication_status)
                           VALUES (%s, %s, %s, %s, %s, %s::vector,
                                   %s, %s, %s, %s, %s, %s, 'published')""",
                        (chunk["chunk_id"], document_id, chunk["page"],
                         index, chunk["content"], vector_literal,
                         chunk.get("source_start"), chunk.get("source_end"),
                         Jsonb(chunk.get("heading_path", [])),
                         chunk.get("parser_version", "legacy"),
                         chunk.get("validation_status", "validated"),
                         Jsonb(chunk.get("validation_reasons", []))),
                    )
        return len(chunks)

    def stage_document(self, result: dict, source_filename: str, source_pdf: bytes) -> None:
        """待审核数据和原件单独存储，不生成向量、不进入正式索引。"""
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (result["document_id"],))
                cursor.execute("SELECT id FROM documents WHERE id = %s", (result["document_id"],))
                if cursor.fetchone() is not None:
                    raise DocumentIdConflictError("文档编号已存在")
                cursor.execute(
                    """INSERT INTO document_review_queue
                       (id, title, source_filename, source_pdf, ingestion_result)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (result["document_id"], result["title"], source_filename,
                     source_pdf, Jsonb(result)),
                )

    def get_staged_document(self, document_id: str) -> dict | None:
        with self._connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """SELECT id, title, source_filename, source_pdf, ingestion_result,
                              approved_pages, reviewer, reviewed_at
                       FROM document_review_queue WHERE id = %s""",
                    (document_id,),
                )
                item = cursor.fetchone()
                if item is None:
                    return None
                cursor.execute(
                    """SELECT reviewer, approved_pages, created_at
                       FROM document_review_events WHERE document_id = %s ORDER BY id""",
                    (document_id,),
                )
                item["review_events"] = cursor.fetchall()
                return item

    def publish_staged_pages(self, document_id: str, pages: list[int], reviewer: str) -> dict:
        """显式批准指定页面；业务事务内写入文档、片段和审核回执。"""
        staged = self.get_staged_document(document_id)
        if staged is None:
            raise LookupError("待审核文档不存在")
        if not pages or len(set(pages)) != len(pages) or any(
            not isinstance(p, int) or isinstance(p, bool) or p < 1 for p in pages
        ):
            raise ValueError("批准页码必须是不重复的正整数")
        result = staged["ingestion_result"]
        page_set = {page["page"] for page in result["pages"]}
        if not set(pages) <= page_set:
            raise ValueError("批准页码不属于该文档")
        selected = [chunk for chunk in result["chunks"] if chunk["page"] in pages]
        if not selected or set(pages) != {chunk["page"] for chunk in selected}:
            raise ValueError("所选页面没有可发布的文字片段")
        vectors = self.embedding_service.embed_documents([chunk["content"] for chunk in selected])
        if len(vectors) != len(selected):
            raise ValueError("向量数量与批准片段数量不一致")
        literals = [self._build_vector_literal(vector) for vector in vectors]

        with self._connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """SELECT ingestion_result, approved_pages, title, source_filename, source_pdf
                       FROM document_review_queue WHERE id = %s FOR UPDATE""",
                    (document_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise LookupError("待审核文档不存在")
                approved = set(current["approved_pages"] or [])
                if set(pages) & approved:
                    raise ValueError("页面已发布，不能重复批准")
                if current["ingestion_result"] != result:
                    raise ValueError("审核期间文档发生变化，请重新核对")
                cursor.execute("SELECT origin_review_id FROM documents WHERE id = %s FOR UPDATE", (document_id,))
                existing = cursor.fetchone()
                if existing is not None and existing["origin_review_id"] != document_id:
                    raise ValueError("文档编号已被其他文档使用")
                if existing is None:
                    cursor.execute(
                        """INSERT INTO documents
                           (id, title, source_filename, page_count, source_pdf, page_audit,
                            parser_version, publication_status, origin_review_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 'partial_published', %s)""",
                        (document_id, current["title"], current["source_filename"],
                         result["page_count"], current["source_pdf"],
                         Jsonb(result["pages"]), selected[0].get("parser_version", "legacy"),
                         document_id),
                    )
                for chunk, literal in zip(selected, literals):
                    cursor.execute(
                        """INSERT INTO knowledge_chunks
                           (id, document_id, page, chunk_index, content, embedding,
                            source_start, source_end, heading_path, parser_version,
                            validation_status, validation_reasons, publication_status)
                           VALUES (%s, %s, %s, %s, %s, %s::vector,
                                   %s, %s, %s, %s, 'approved', %s, 'published')""",
                        (chunk["chunk_id"], document_id, chunk["page"],
                         chunk.get("chunk_index", chunk["page"] * 100000 +
                                   int(chunk["chunk_id"].rsplit("-c", 1)[1])),
                         chunk["content"], literal,
                         chunk.get("source_start"), chunk.get("source_end"),
                         Jsonb(chunk.get("heading_path", [])),
                         chunk.get("parser_version", "legacy"),
                         Jsonb(chunk.get("validation_reasons", []))),
                    )
                combined = sorted(approved | set(pages))
                all_pages = {page["page"] for page in result["pages"]}
                fully_published = set(combined) == all_pages
                cursor.execute(
                    """UPDATE document_review_queue SET approved_pages = %s, reviewer = %s,
                              reviewed_at = NOW() WHERE id = %s""",
                    (combined, reviewer, document_id),
                )
                cursor.execute(
                    """INSERT INTO document_review_events (document_id, reviewer, approved_pages)
                       VALUES (%s, %s, %s)""",
                    (document_id, reviewer, sorted(pages)),
                )
                cursor.execute(
                    "UPDATE documents SET publication_status = %s WHERE id = %s",
                    ("published" if fully_published else "partial_published", document_id),
                )
        return {"document_id": document_id, "published_pages": combined,
                "remaining_pages": sorted(all_pages - set(combined)),
                "published_chunk_count": len(selected)}

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
                    WHERE chunk.publication_status = 'published'
                      AND document.publication_status IN ('published', 'partial_published')
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
