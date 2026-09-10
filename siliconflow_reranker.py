"""硅基流动 BGE Reranker 的 HTTP 适配器。

这个文件只负责两件事：
1. 把项目内部的 ``score(query, documents)`` 调用转换成 HTTP 请求；
2. 把云服务按相关性排序的响应，还原为与输入 documents 相同的顺序。

业务检索代码不依赖硅基流动的响应格式，因此以后换成本地模型或其他供应商时，
只需要新增适配器，不必重写混合检索流程。
"""

import os

import httpx
from dotenv import dotenv_values

from reranker import RerankerService, RerankerUnavailableError


RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class SiliconFlowReranker:
    """调用硅基流动的免费 BGE 模型，为候选文档计算相关性分数。"""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        # 尽早拒绝空 Key，避免真正检索时才得到难以理解的 401 错误。
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key 必须是非空字符串")
        self.api_key = api_key.strip()
        self.client = client
        self.model = model

    def score(self, query: str, documents: list[str]) -> list[float]:
        """返回与 documents 原始顺序一一对应的相关性分数。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")
        if not documents:
            return []
        if any(not isinstance(document, str) or not document.strip() for document in documents):
            raise ValueError("documents 中的每一项都必须是非空字符串")

        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            # 必须返回全部候选，才能为每个输入文档恢复一个分数。
            "top_n": len(documents),
            # 我们已经保留原始文本，无需让响应重复传输文档内容。
            "return_documents": False,
        }

        # 测试时注入 MockTransport 客户端；生产环境才创建真实网络客户端。
        owns_client = self.client is None
        client = self.client or httpx.Client(
            timeout=httpx.Timeout(15, connect=5),
            trust_env=False,
        )
        try:
            response = client.post(
                RERANK_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # 不把底层异常文本直接返回给 API 用户，避免日志意外夹带敏感请求信息。
            raise RerankerUnavailableError("Reranker 请求失败") from exc
        finally:
            if owns_client:
                client.close()

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise RerankerUnavailableError("Reranker 响应缺少 results")

        # 云服务返回的是“按相关性排好序”的列表；index 才代表原输入位置。
        # 而 rerank_candidates 会按原候选顺序 zip 分数，所以这里必须恢复原顺序。
        score_by_index: dict[int, float] = {}
        for item in results:
            if not isinstance(item, dict):
                raise RerankerUnavailableError("Reranker 返回了无效结果项")
            index = item.get("index")
            relevance_score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not isinstance(relevance_score, (int, float))
                or isinstance(relevance_score, bool)
            ):
                raise RerankerUnavailableError("Reranker 返回了无效索引或分数")
            score_by_index[index] = float(relevance_score)

        expected_indexes = set(range(len(documents)))
        if set(score_by_index) != expected_indexes:
            raise RerankerUnavailableError("Reranker 返回的候选数量或索引不完整")
        return [score_by_index[index] for index in range(len(documents))]


def build_configured_reranker() -> RerankerService | None:
    """从环境读取 Key；未配置时返回 None，让系统继续使用 RRF。

    ``os.environ`` 适合 Docker/部署环境，``.env`` 方便本地学习；环境变量优先，
    从而无需为了部署去修改代码。
    """
    env_file = dotenv_values(".env")
    api_key = (os.getenv("SILICONFLOW_API_KEY") or env_file.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        return None
    return SiliconFlowReranker(api_key)
