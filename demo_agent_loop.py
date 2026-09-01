"""离线演示：预设模型先查订单、再查工单、最后根据实际工具结果汇总。

模型响应由 MockTransport 模拟；不读取 .env，不请求真实模型。
查询函数、参数校验和 Agent 循环使用项目代码。
"""

import json

import httpx

from agent_loop import run_agent


def run_demo(max_steps: int = 5) -> dict:
    planned_calls = [
        ("query_order", {"order_id": "O-2001"}),
        ("query_ticket", {"ticket_id": "T-1003"}),
    ]
    request_count = 0
    selected_tools = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count <= len(planned_calls):
            name, args = planned_calls[request_count - 1]
            selected_tools.append(name)
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": f"demo_call_{request_count}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }
        elif request_count == 3:
            payload = json.loads(request.content)
            order, ticket = [
                json.loads(item["content"])
                for item in payload["messages"] if item["role"] == "tool"
            ]
            message = {
                "role": "assistant",
                "content": (
                    f"[离线模拟] 订单 {order['id']}：{order['product']}，状态 {order['status']}；"
                    f"工单 {ticket['id']}：状态 {ticket['status']}，优先级 {ticket['priority']}。"
                ),
            }
        else:
            raise AssertionError("收到最终回答后不应继续请求。")
        return httpx.Response(200, json={"choices": [{"message": message}]})

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        answer = run_agent(
            "mock-only-not-a-real-key", client,
            "查询订单 O-2001 和工单 T-1003，汇总它们的状态。", max_steps=max_steps,
        )
    return {
        "is_mock": True, "model_requests": 0,
        "simulated_model_requests": request_count,
        "tool_names": selected_tools, "answer": answer,
    }


if __name__ == "__main__":
    print("离线多轮示例；模型回复为预设流程，不代表真实模型能力：")
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
