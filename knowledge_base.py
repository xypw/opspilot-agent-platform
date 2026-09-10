"""OpsPilot 的最小知识库检索基线。

已上传文档走“向量召回 + 关键词召回 + RRF 融合 + 可选 Reranker”。后续替换为
PostgreSQL 全文检索和 pgvector 时，仍保持相同的返回结构。
"""

import logging

from embedding_service import LocalEmbeddingService
from hybrid_search import hybrid_search
from keyword_search import keyword_search
from reranker import RerankerUnavailableError
from siliconflow_reranker import build_configured_reranker
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
# 没有配置免费 Reranker Key 时值为 None，混合检索会安全地停留在 RRF 排名。
RERANKER = build_configured_reranker()
LOGGER = logging.getLogger(__name__)


def index_knowledge_chunks(chunks: list[dict]) -> None:
    """上传文档后，把切分结果写入共享向量库。"""
    VECTOR_STORE.add(chunks)


def search_knowledge_base(query: str, limit: int = 3) -> list[dict]:
    """上传文档使用混合检索；暂无上传数据时搜索内置样例。"""
    if VECTOR_STORE.records:
        try:
            hybrid_results = hybrid_search(
                query,
                VECTOR_STORE.records,
                VECTOR_STORE.embedding_service,
                limit,
                reranker=RERANKER,
            )
        except RerankerUnavailableError:
            # 重排是“提升质量”的可选阶段，短暂故障不应该让整个知识问答接口失败。
            LOGGER.warning("Reranker 暂不可用，本次检索降级为 RRF", exc_info=True)
            hybrid_results = hybrid_search(
                query,
                VECTOR_STORE.records,
                VECTOR_STORE.embedding_service,
                limit,
            )
        return [
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"semantic_score", "keyword_score", "rrf_score", "rerank_score"}
                },
                # 有精排分数就代表最终排序由模型决定；否则使用融合分数。
                "score": result.get("rerank_score", result["rrf_score"]),
            }
            for result in hybrid_results
        ]
    keyword_results = keyword_search(query, KNOWLEDGE_CHUNKS, limit)
    return [
        {
            **{key: value for key, value in result.items() if key != "keyword_score"},
            "score": result["keyword_score"],
        }
        for result in keyword_results
    ]
