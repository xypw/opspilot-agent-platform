"""验收 FastAPI /chat -> Agent 工具分发 -> Java 订单服务，不调用真实模型。"""

from fastapi.testclient import TestClient

from main import app


def main() -> int:
    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={"message": "帮我查询订单 O-2003", "mode": "mock"},
        )
    response.raise_for_status()
    result = response.json()
    assert result["tool_name"] == "query_order"
    assert result["tool_result"] == {
        "id": "O-2003",
        "status": "cancelled",
        "product": "显示器",
        "delivered_at": None,
        "amount_cents": 159900,
    }
    assert result["model_requests"] == 0
    print("/chat 已通过 Java 查询 O-2003；真实模型请求次数：0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
