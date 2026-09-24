"""在已运行的 API 容器执行本机部署验收；仅虚构订单，模型使用 MockTransport。

显式传入 --allow-demo-write 才创建并确认一笔演示申请；保留记录用于幂等核验。
不打印鉴权令牌、请求头、数据库地址或模型密钥。
"""
import argparse
import json
import os
from pathlib import Path
import sys
from time import sleep
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import redis
from evidence_review import ConfiguredEvidenceReviewer
from model_rate_limit import ModelRateLimitExceeded, RedisModelRateLimiter
from preview_tool_call import request_message
from retry_policy import ModelRequestTelemetry, request_message_with_retry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-demo-write", action="store_true")
    args = parser.parse_args()
    result = {}
    configured = json.loads(os.environ["OPSPILOT_AUTH_TOKENS"])
    token = next(key for key, value in configured.items() if value["user_id"] == "U-1001")
    with httpx.Client(base_url="http://127.0.0.1:8011", trust_env=False, timeout=15) as client:
        for path in ("/health", "/demo", "/service", "/openapi.json"):
            assert client.get(path).status_code == 200, path
        result["pages_and_health"] = "passed"
        assert client.get("/service/orders/O-2001").status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        assert client.get("/service/orders/O-2001").status_code == 200
        assert client.get("/service/orders/O-2002").status_code == 404
        result["identity_and_order_ownership"] = "passed"
        if args.allow_demo_write:
            response = client.post("/service/orders/O-2001/return-drafts")
            assert response.status_code == 200, "demo draft creation failed"
            draft = response.json()
            path = "/service/orders/O-2001/return-drafts/" + draft["draft_id"]
            if draft["status"] in {"EXPIRED", "CANCELLED"}:
                refreshed = client.post(path + "/refresh")
                assert refreshed.status_code == 200, "cannot refresh demo draft"
                draft = refreshed.json()
                path = "/service/orders/O-2001/return-drafts/" + draft["draft_id"]
            if draft["status"] == "WAITING_REASON":
                reviewed = client.post(path + "/reason", json={
                    "return_reason": "本机部署验收用虚构申请", "reason_code": "PERSONAL_PREFERENCE",
                })
                assert reviewed.status_code == 200, "demo review failed"
                draft = client.get(path).json()
            assert draft["status"] in {"REVIEWED", "SUBMITTED"}
            # 已提交的演示记录只做回放，不为获得新样本删除原有申请。
            initial_status = draft["status"]
            snapshot = {key: draft[key] for key in ("product", "amount_cents", "expires_at")}
            assert client.post(path + "/confirm", json={**snapshot, "amount_cents": 1}).status_code == 409
            assert client.get(path).json()["status"] == initial_status
            first = client.post(path + "/confirm", json=snapshot)
            second = client.post(path + "/confirm", json=snapshot)
            assert first.status_code == second.status_code == 200
            assert first.json()["application_id"] == second.json()["application_id"]
            result["confirmation_and_duplicate_request"] = "same_application"
            result["draft_status_before_confirmation"] = initial_status
            result["demo_draft_id"] = draft["draft_id"]

    scope = "deployment-test-" + uuid4().hex
    redis_client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2)
    limiter = RedisModelRateLimiter(redis_client, scope=scope, limit=3, window_seconds=60)
    calls = []
    real_client = httpx.Client
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "1305"}})
        content = "ok" if len(calls) == 2 else json.dumps({
            "supported": True, "supporting_quotes": [{"chunk_id": "demo", "text": "审核通过后三个工作日到账。"}],
            "missing_information": "",
        }, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
    def client_factory(**_kwargs):
        return real_client(transport=httpx.MockTransport(respond), trust_env=False)
    try:
        with patch.dict(os.environ, {"MODEL_RATE_LIMIT_SCOPE": scope, "MODEL_RATE_LIMIT_REQUESTS": "3",
                                     "MODEL_RATE_LIMIT_WINDOW_SECONDS": "60"}):
            telemetry = ModelRequestTelemetry()
            with client_factory() as client:
                request_message_with_retry("synthetic", client, [], sleeper=lambda _: None, telemetry=telemetry)
                assert telemetry.http_attempts == 2 and telemetry.retry_count == 1
            with patch("evidence_review.load_api_key", return_value="synthetic"), patch(
                "evidence_review.httpx.Client", side_effect=client_factory,
            ):
                ConfiguredEvidenceReviewer()("退款多久到账？", [{"chunk_id": "demo", "title": "演示政策",
                    "page": 1, "content": "审核通过后三个工作日到账。"}])
            with client_factory() as client:
                try:
                    request_message("synthetic", client, [])
                except ModelRateLimitExceeded:
                    pass
                else:
                    raise AssertionError("request must be blocked")
            assert len(calls) == 3 and int(redis_client.get(limiter.key)) == 3
            result["shared_retry_and_evidence_budget"] = {"mock_http_attempts": 3, "blocked_fourth": True}
            redis_client.pexpire(limiter.key, 20)
            sleep(0.05)
            limiter.acquire()
            assert int(redis_client.get(limiter.key)) == 1
            result["quota_expiry_recovery"] = "passed"
    finally:
        redis_client.delete(limiter.key)
        redis_client.close()
    print(json.dumps({"mode": "deployed_services_mock_model", "external_model_requests": 0,
                      "checks": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
