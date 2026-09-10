"""Reranker HTTP 适配器测试：全程使用假响应，不访问外网。"""

import json
import unittest

import httpx

from reranker import RerankerUnavailableError
from siliconflow_reranker import RERANK_URL, SiliconFlowReranker


class SiliconFlowRerankerTests(unittest.TestCase):
    def test_scores_are_restored_to_original_document_order(self):
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), RERANK_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer fake-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["top_n"], 2)
            self.assertFalse(payload["return_documents"])

            # 模拟云服务的真实行为：相关文档排第一，所以响应顺序是 1、0。
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.96},
                        {"index": 0, "relevance_score": 0.08},
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        reranker = SiliconFlowReranker("fake-key", client=client)

        scores = reranker.score(
            "退款多久到账",
            ["电子发票会发送到邮箱。", "退款三个工作日到账。"],
        )

        # 返回值必须对应原 documents 顺序，而不是云服务的排名顺序。
        self.assertEqual(scores, [0.08, 0.96])
        client.close()

    def test_network_failure_becomes_safe_domain_error(self):
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("测试超时", request=request)

        client = httpx.Client(transport=httpx.MockTransport(fail), trust_env=False)
        reranker = SiliconFlowReranker("fake-key", client=client)

        with self.assertRaises(RerankerUnavailableError):
            reranker.score("退款", ["退款规则"])
        client.close()

    def test_incomplete_indexes_are_rejected(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"results": [{"index": 0, "relevance_score": 0.5}]},
                )
            ),
            trust_env=False,
        )
        reranker = SiliconFlowReranker("fake-key", client=client)

        with self.assertRaises(RerankerUnavailableError):
            reranker.score("退款", ["文档一", "文档二"])
        client.close()


if __name__ == "__main__":
    unittest.main()
