"""第 3 天：有次数上限的 Agent 循环，尚未接入 /chat。

每轮最多执行一个只读工具；无工具调用时返回文字，错误直接停止，不自动重试。
max_steps 限制模型请求总次数，最终回答也占一次；达到上限时不再追加请求。
"""

import httpx

from grounding_policy import answer_when_evidence_is_missing, append_verified_citations
from preview_tool_call import build_initial_messages, extract_tool_preview
from retry_policy import request_message_with_retry
from tool_executor import execute_tool
from tool_messages import build_tool_message


def run_agent(api_key: str, client: httpx.Client, question: str, max_steps: int = 5) -> str:
    """返回模型的最终文字；需要澄清时也可返回询问，不代表业务任务已成功。"""
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps 必须是正整数。")

    messages = build_initial_messages(question)
    knowledge_evidence = []
    # 旧流程限制每次只查一条；新循环允许按需分轮查询，但每轮最多一个工具。
    messages[0]["content"] = (
        "你是企业知识、工单与订单助手。工单查询用 query_ticket，订单查询用 query_order。"
        "政策、流程和规范问题用 search_knowledge_base，并且只能依据返回片段回答。"
        "修改工单优先级只能用 request_priority_change 创建待确认操作；"
        "必须把 action_id 告诉用户并等待确认，不能声称已经修改。"
        "按用户要求分轮查询，每轮最多调用一个工具；仅查询用户提供的编号。"
        "收到工具结果后，信息足够就用中文回答，否则继续查询必要的记录；引用由程序统一附加。"
        "不得编造状态或商品；null 表示未找到记录。缺少必要编号时先询问用户。"
    )

    for step in range(max_steps):
        # 这里只有模型 HTTP 请求会被重试；下一行之后的工具执行永远不会被本策略重复调用。
        message = request_message_with_retry(api_key, client, messages)

        # 先检查消息格式，再决定结束还是执行工具。
        if message.get("role") != "assistant":
            raise ValueError("模型消息的角色不正确。")
        calls = message.get("tool_calls")
        if calls is not None and not isinstance(calls, list):
            raise ValueError("tool_calls 必须是列表或 null。")

        if not calls:
            answer = message.get("content")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("模型没有返回非空文字回答。")
            return append_verified_citations(answer, knowledge_evidence)

        preview = extract_tool_preview(message)
        tool_call = calls[0]
        tool_call_id = tool_call.get("id")
        if tool_call.get("type") != "function":
            raise ValueError("工具调用类型不正确；未执行查询。")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            raise ValueError("缺少有效的工具调用 ID；未执行查询。")

        result = execute_tool(preview["name"], preview["arguments"])
        no_evidence_answer = answer_when_evidence_is_missing(preview["name"], result)
        if no_evidence_answer is not None:
            return no_evidence_answer
        if preview["name"] == "search_knowledge_base":
            knowledge_evidence.extend(result)
        tool_message = build_tool_message(tool_call_id, result)

        # 原始调用请求在前，对应结果在后；下一轮模型会收到完整历史。
        messages.append(message)
        messages.append(tool_message)

    raise RuntimeError("达到最大请求次数，任务尚未完成")
