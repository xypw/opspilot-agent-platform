"""Reranker 契约与候选精排流程；具体模型可以独立替换。"""

from typing import Protocol


class RerankerUnavailableError(RuntimeError):
    """重排服务暂时不可用。

    单独定义这个异常，是为了让上层只捕获“允许降级”的故障。
    例如网络超时可以退回 RRF，但代码写错造成的 TypeError 不应该被悄悄吞掉。
    """


class RerankerService(Protocol):
    """真实本地模型和测试假模型都遵循这个接口。"""

    def score(self, query: str, documents: list[str]) -> list[float]:
        """返回每个 query-document 对的相关性分数。"""


def rerank_candidates(
    query: str,
    candidates: list[dict],
    service: RerankerService,
    limit: int = 3,
) -> list[dict]:
    """让精排服务重打分候选，并返回最终 Top-K。

    输入是 RRF 已召回的候选片段，输出仍是相同的片段字典，只额外增加
    ``rerank_score``。保持返回结构稳定，调用方就不需要知道背后换了哪种模型。
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是正整数")
    if not candidates:
        return []

    # 标题通常包含章节主题，正文包含具体答案；拼在一起比只给正文更利于模型判断。
    documents = [
        f"{candidate.get('title', '')}\n{candidate.get('content', '')}"
        for candidate in candidates
    ]
    scores = service.score(query, documents)
    if len(scores) != len(candidates):
        raise ValueError("候选数量与 Reranker 分数数量不一致")

    # 不直接修改 candidates，避免同一批召回结果被日志或其他评测复用时受到污染。
    results = []
    for candidate, score in zip(candidates, scores):
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("Reranker 分数必须是数字")
        results.append({**candidate, "rerank_score": float(score)})

    results.sort(key=lambda item: (-item["rerank_score"], item["chunk_id"]))
    return results[:limit]
