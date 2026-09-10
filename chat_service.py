"""聊天服务入口：显式选择模拟或真实模型，不静默降级。"""

import json
import re

import httpx

from agent_roundtrip import run_roundtrip
from preview_tool_call import load_api_key


class MockInputError(ValueError):
    """规则模拟无法选择一个明确的工单或订单编号。"""


class ChatConfigurationError(ValueError):
    """真实模式缺少可用的本地密钥配置。"""


def run_mock_chat(message: str) -> dict:
    """按编号和有限关键词模拟查询或修改申请，不代表大模型语义能力。

    模拟的是模型的 HTTP 响应；参数校验、查询、工具消息仍运行项目的真实代码。
    """
    ids = set(re.findall(r"(?<![A-Za-z0-9_-])[TO]-[0-9]{4}(?![A-Za-z0-9_-])", message))
    if len(ids) != 1:
        raise MockInputError("模拟模式每次只支持一个编号：工单 T-四位数字或订单 O-四位数字，例如 T-1003 或 O-2003。")
    record_id = ids.pop()
    change_requested = any(word in message for word in ("改", "设置", "调整")) or bool(
        re.search(r"\b(change|set|update)\b", message, flags=re.IGNORECASE)
    )
    priorities = {
        value.lower()
        for value in re.findall(r"(?<![A-Za-z])(low|medium|high)(?![A-Za-z])", message, flags=re.IGNORECASE)
    }
    return_requested = any(word in message for word in ("退货", "无理由", "能退", "可以退"))

    if change_requested:
        if not record_id.startswith("T-") or len(priorities) != 1:
            raise MockInputError(
                "模拟修改需要且只支持一个工单编号和一个新优先级：low、medium 或 high。"
            )
        new_priority = priorities.pop()
        tool_name, record_label = "request_priority_change", "工单"
        arguments = {"ticket_id": record_id, "new_priority": new_priority}
    elif record_id.startswith("O-") and return_requested:
        tool_name, record_label = "check_return_eligibility", "订单"
        arguments = {"order_id": record_id}
    elif record_id.startswith("T-"):
        tool_name, argument_name, record_label = "query_ticket", "ticket_id", "工单"
        arguments = {argument_name: record_id}
    else:
        tool_name, argument_name, record_label = "query_order", "order_id", "订单"
        arguments = {argument_name: record_id}
    request_count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            reply = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "mock_call_001",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            }
        elif request_count == 2:
            payload = json.loads(request.content)
            tool_result = json.loads(payload["messages"][-1]["content"])
            if tool_result is None:
                answer = f"[模拟模式] 未找到{record_label} {record_id}。"
            elif tool_name == "query_ticket":
                answer = f"[模拟模式] 工单 {tool_result['id']}：状态 {tool_result['status']}，优先级 {tool_result['priority']}。"
            elif tool_name == "request_priority_change":
                answer = (
                    f"[模拟模式] 已创建待确认操作 {tool_result['action_id']}，"
                    f"计划把工单 {tool_result['ticket_id']} 的优先级从 "
                    f"{tool_result['previous_priority']} 改为 {tool_result['new_priority']}。"
                    "当前状态 pending，尚未修改工单；请确认该 action_id 后再执行。"
                )
            elif tool_name == "check_return_eligibility":
                decision = tool_result["decision"]
                if decision == "NO_REASON_ALLOWED":
                    answer = f"[模拟模式] 订单 {record_id} 在七天无理由期限内，可以申请退货。"
                elif decision == "REASON_REQUIRED":
                    answer = f"[模拟模式] 订单 {record_id} 可以申请退货，但必须提供退货理由。"
                else:
                    answer = f"[模拟模式] 订单 {record_id} 当前不能申请退货。"
            else:
                answer = f"[模拟模式] 订单 {tool_result['id']}：商品 {tool_result['product']}，状态 {tool_result['status']}。"
            reply = {"role": "assistant", "content": answer}
        else:
            raise ValueError("模拟请求超过两次，流程已停止。")
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    # MockTransport 在本地返回响应，不建立任何外部网络连接。
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        trace = run_roundtrip("mock-only-not-a-real-key", client, question=message)
    return {
        **trace,
        "mode": "mock",
        "is_mock": True,
        "model": "mock",
        "model_requests": 0,
        "simulated_model_requests": request_count,
    }


def run_live_chat(message: str) -> dict:
    try:
        api_key = load_api_key()
    except (ValueError, OSError):
        raise ChatConfigurationError("真实模式未配置可用密钥；请在本地 .env 中设置，不要通过请求体传密钥。") from None
    with httpx.Client(timeout=httpx.Timeout(45, connect=10), trust_env=False) as client:
        trace = run_roundtrip(api_key, client, question=message)
    # 只返回教学所需字段；不会返回原始请求头、密钥或整段对话。
    trace["answer"] = trace["answer"].replace(api_key, "[REDACTED]")
    return {**trace, "mode": "live", "is_mock": False, "simulated_model_requests": 0}


def chat(message: str, mode: str) -> dict:
    if mode == "mock":
        return run_mock_chat(message)
    if mode == "live":
        return run_live_chat(message)
    raise ValueError("不支持的聊天模式。")
