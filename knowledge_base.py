"""OpsPilot 的最小知识库检索基线。

当前先用内存中的知识片段和关键词重叠分数跑通工具闭环。后续会保持相同的
返回结构，把检索实现替换为 PostgreSQL 全文检索、pgvector 和重排序。
"""

import re

from embedding_service import LocalEmbeddingService
from vector_store import InMemoryVectorStore


KNOWLEDGE_CHUNKS = [
    {
        "chunk_id": "refund-policy-p2-c1",
        "title": "售后与退款制度",
        "page": 2,
        "content": "退款申请审核通过后，款项通常会在三个工作日内原路到账。",
    },
    {
        "chunk_id": "ticket-sla-p4-c1",
        "title": "客户支持服务规范",
        "page": 4,
        "content": "高优先级工单应在二十分钟内首次响应，并持续记录处理进展。",
    },
    {
        "chunk_id": "invoice-guide-p1-c1",
        "title": "电子发票申请指南",
        "page": 1,
        "content": "订单完成后可申请电子发票，发票会发送到用户填写的邮箱。",
    },
]

# 当前单进程教学版共享这个对象；后续替换为 PostgreSQL/pgvector。
VECTOR_STORE = InMemoryVectorStore(LocalEmbeddingService())


def index_knowledge_chunks(chunks: list[dict]) -> None:
    """上传文档后，把切分结果写入共享向量库。"""
    VECTOR_STORE.add(chunks)


def _search_terms(text: str) -> set[str]:
    """英文按单词匹配；连续中文生成二元词片，作为向量检索前的简单基线。"""
    normalized = text.lower().strip()
    terms = set(re.findall(r"[a-z0-9_-]+", normalized))
    for chinese_text in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(chinese_text) == 1:
            terms.add(chinese_text)
        else:
            terms.update(chinese_text[index:index + 2] for index in range(len(chinese_text) - 1))
    return terms


def search_knowledge_base(query: str, limit: int = 3) -> list[dict]:
    """优先搜索上传文档；暂无上传数据时使用内置关键词样例。"""
    if VECTOR_STORE.records:
        semantic_results = VECTOR_STORE.search(query, limit)
        return [
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key != "semantic_score"
                },
                "score": result["semantic_score"],
            }
            for result in semantic_results
        ]

    query_terms = _search_terms(query)
    ranked_results = []

    for chunk in KNOWLEDGE_CHUNKS:
        searchable_text = f"{chunk['title']} {chunk['content']}"
        score = len(query_terms & _search_terms(searchable_text))
        if score > 0:
            ranked_results.append({**chunk, "score": score})

    ranked_results.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    return ranked_results[:limit]
