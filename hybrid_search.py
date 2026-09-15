"""用 RRF 融合关键词检索与语义检索的排名。"""

import re

from embedding_service import LocalEmbeddingService
from keyword_search import keyword_search
from reranker import RerankerService, rerank_candidates
from vector_search import semantic_search


_BUSINESS_IDENTIFIER = re.compile(
    r"(?<![a-z0-9_-])[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+(?![a-z0-9_-])"
)


def _has_exact_identifier_match(query: str, result: dict) -> bool:
    """保留错误码、订单号等完整标识符；普通词片重合不绕过语义门槛。"""
    query_ids = set(_BUSINESS_IDENTIFIER.findall(query.casefold()))
    if not query_ids:
        return False
    searchable = f"{result.get('title', '')} {result.get('content', '')}".casefold()
    return bool(query_ids & set(_BUSINESS_IDENTIFIER.findall(searchable)))


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    limit: int = 3,
    rank_constant: int = 60,
) -> list[dict]:
    """只根据各检索器的排名融合结果，不直接相加原始分数。"""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是正整数")
    if not isinstance(rank_constant, int) or isinstance(rank_constant, bool) or rank_constant < 1:
        raise ValueError("rank_constant 必须是正整数")

    fused_records: dict[str, dict] = {}
    rrf_scores: dict[str, float] = {}

    for results in ranked_lists:
        for rank, result in enumerate(results, start=1):
            chunk_id = result.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                raise ValueError("检索结果缺少有效 chunk_id")
            fused_records[chunk_id] = {**fused_records.get(chunk_id, {}), **result}
            rrf_scores[chunk_id] = (
                rrf_scores.get(chunk_id, 0)
                + 1 / (rank_constant + rank)
            )

    fused_results = []
    for chunk_id, record in fused_records.items():
        fused_results.append({**record, "rrf_score": rrf_scores[chunk_id]})

    fused_results.sort(
        key=lambda item: (-item["rrf_score"], -item.get("keyword_score", 0), item["chunk_id"])
    )
    return fused_results[:limit]


def hybrid_search(
    query: str,
    records: list[dict],
    service: LocalEmbeddingService,
    limit: int = 3,
    reranker: RerankerService | None = None,
    *,
    min_semantic_similarity: float | None = None,
) -> list[dict]:
    """低相似度候选拒答；完整业务标识符精确命中可例外。"""
    candidate_limit = limit * 3
    admitted_semantic = semantic_search(
        query, records, service, max(candidate_limit, len(records)),
        min_similarity=min_semantic_similarity,
    )
    semantic_results = admitted_semantic[:candidate_limit]
    keyword_results = keyword_search(query, records, candidate_limit)
    if min_semantic_similarity is not None:
        admitted_ids = {result["chunk_id"] for result in admitted_semantic}
        keyword_results = [
            result for result in keyword_results
            if result["chunk_id"] in admitted_ids
            or _has_exact_identifier_match(query, result)
        ]
    fused_candidates = reciprocal_rank_fusion(
        [semantic_results, keyword_results],
        limit=candidate_limit,
    )
    if reranker is None:
        return fused_candidates[:limit]
    return rerank_candidates(query, fused_candidates, reranker, limit)
