"""内存语义检索基线；后续用相同契约替换为 PostgreSQL/pgvector。"""

import numpy as np

from embedding_service import LocalEmbeddingService


def semantic_search(
    query: str,
    records: list[dict],
    service: LocalEmbeddingService,
    limit: int = 3,
) -> list[dict]:
    """用余弦相似度排列已经带 embedding 的知识片段。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是正整数")
    if not records:
        return []

    query_vector = np.asarray(service.embed_query(query), dtype=float)
    query_length = np.linalg.norm(query_vector)
    if query_vector.ndim != 1 or query_length == 0:
        raise ValueError("查询向量必须是一维非零向量")

    results = []
    for record in records:
        record_vector = np.asarray(record.get("embedding"), dtype=float)
        if record_vector.ndim != 1 or record_vector.shape != query_vector.shape:
            raise ValueError("文档向量与查询向量维度不一致")
        record_length = np.linalg.norm(record_vector)
        if record_length == 0:
            raise ValueError("文档向量不能是零向量")

        score = float(np.dot(query_vector, record_vector) / (query_length * record_length))
        result = {key: value for key, value in record.items() if key != "embedding"}
        result["semantic_score"] = score
        results.append(result)

    results.sort(key=lambda item: item["semantic_score"], reverse=True)
    return results[:limit]
