"""OpsPilot 检索评测：用固定测试集比较不同检索方案。

这个模块故意不绑定 FastAPI、数据库或某个模型供应商。只要一个函数能够接收问题
并返回带 ``chunk_id`` 的结果，它就能被放进同一套评测中比较。
"""

from collections.abc import Callable

from embedding_service import LocalEmbeddingService
from hybrid_search import hybrid_search
from reranker import RerankerService
from vector_search import semantic_search


RetrievalFunction = Callable[[str, int], list[dict]]


def _validate_runs(runs: list[dict]) -> None:
    """验证指标计算所需的最小字段，避免错误数据悄悄算出一个假指标。"""
    if not isinstance(runs, list) or not runs:
        raise ValueError("runs 必须是非空列表")

    for run in runs:
        expected_id = run.get("expected_chunk_id") if isinstance(run, dict) else None
        retrieved_ids = run.get("retrieved_chunk_ids") if isinstance(run, dict) else None
        if not isinstance(expected_id, str) or not expected_id.strip():
            raise ValueError("每条 run 都必须包含非空 expected_chunk_id")
        if not isinstance(retrieved_ids, list) or any(
            not isinstance(chunk_id, str) or not chunk_id.strip()
            for chunk_id in retrieved_ids
        ):
            raise ValueError("每条 run 都必须包含字符串列表 retrieved_chunk_ids")


def _validate_k(k: int) -> None:
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError("k 必须是正整数")


def calculate_recall_at_k(runs: list[dict], k: int) -> float:
    """计算 Recall@K：标准答案是否至少出现在前 K 个检索结果中。

    每一个问题只记 0 或 1：找到了就是命中，没找到就是未命中。最后取平均值，
    因此结果位于 0 到 1 之间。
    """
    _validate_runs(runs)
    _validate_k(k)

    hit_count = 0
    for run in runs:
        top_k = run["retrieved_chunk_ids"][:k]
        if run["expected_chunk_id"] in top_k:
            hit_count += 1
    return hit_count / len(runs)


def calculate_mrr_at_k(runs: list[dict], k: int) -> float:
    """计算 MRR@K：正确答案越靠前，贡献越大。

    第 1 名贡献 1，第 2 名贡献 1/2；若正确答案不在前 K 名，贡献为 0。
    ``list.index`` 返回从 0 开始的下标，所以计算真实排名时必须加 1。
    """
    _validate_runs(runs)
    _validate_k(k)

    total_score = 0.0
    for run in runs:
        top_k = run["retrieved_chunk_ids"][:k]
        expected_id = run["expected_chunk_id"]
        if expected_id in top_k:
            rank = top_k.index(expected_id) + 1
            total_score += 1 / rank
    return total_score / len(runs)


def evaluate_retriever(
    name: str,
    retrieve: RetrievalFunction,
    cases: list[dict],
    k: int = 3,
) -> dict:
    """运行一个检索方案，并同时输出汇总指标和逐题结果。

    ``runs`` 不能省略：总体分数告诉我们“好不好”，逐题结果才能定位“为什么不好”。
    后续我们会把这些失败样本加入回归测试集，防止优化后旧问题重新退化。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name 必须是非空字符串")
    if not callable(retrieve):
        raise ValueError("retrieve 必须是可调用函数")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 必须是非空列表")
    _validate_k(k)

    runs = []
    for case in cases:
        query = case.get("query") if isinstance(case, dict) else None
        expected_id = case.get("expected_chunk_id") if isinstance(case, dict) else None
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("每条 case 都必须包含非空 id")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("每条 case 都必须包含非空 query")
        if not isinstance(expected_id, str) or not expected_id.strip():
            raise ValueError("每条 case 都必须包含非空 expected_chunk_id")

        # 每个方案都拿到相同的 query 与 k，保证横向比较公平。
        results = retrieve(query, k)
        if not isinstance(results, list):
            raise ValueError("检索函数必须返回列表")
        retrieved_ids = [result.get("chunk_id") for result in results if isinstance(result, dict)]
        if len(retrieved_ids) != len(results):
            raise ValueError("每个检索结果都必须是包含 chunk_id 的字典")
        runs.append(
            {
                "case_id": case_id,
                "query": query,
                "expected_chunk_id": expected_id,
                "retrieved_chunk_ids": retrieved_ids,
            }
        )

    return {
        "retriever": name,
        "case_count": len(runs),
        "k": k,
        "recall_at_k": calculate_recall_at_k(runs, k),
        "mrr_at_k": calculate_mrr_at_k(runs, k),
        "runs": runs,
    }


def compare_retrievers(
    cases: list[dict],
    records: list[dict],
    embedding_service: LocalEmbeddingService,
    *,
    reranker: RerankerService | None = None,
    k: int = 3,
) -> list[dict]:
    """在同一份评测集上比较语义、混合和混合加重排三种方案。"""
    retrievers: list[tuple[str, RetrievalFunction]] = [
        (
            "semantic",
            lambda query, limit: semantic_search(query, records, embedding_service, limit),
        ),
        (
            "hybrid_rrf",
            lambda query, limit: hybrid_search(query, records, embedding_service, limit),
        ),
    ]
    if reranker is not None:
        retrievers.append(
            (
                "hybrid_rrf_rerank",
                lambda query, limit: hybrid_search(
                    query,
                    records,
                    embedding_service,
                    limit,
                    reranker=reranker,
                ),
            )
        )

    return [
        evaluate_retriever(name, retrieve, cases, k)
        for name, retrieve in retrievers
    ]
