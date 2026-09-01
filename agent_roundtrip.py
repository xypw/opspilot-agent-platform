"""双工具可选的查询闭环：最多两次模型请求、一次只读查询；不自动重试。"""

import json

import httpx
from pydantic import ValidationError

from grounding_policy import answer_when_evidence_is_missing, append_verified_citations
from tool_executor import execute_tool
from preview_tool_call import (
    DEMO_QUESTION,
    MODEL,
    build_initial_messages,
    extract_tool_preview,
    load_api_key,
    request_message,
)
from tool_messages import build_tool_message


def run_roundtrip(api_key: str, client: httpx.Client, question: str = DEMO_QUESTION) -> dict:
    """把学习者完成的查询、参数校验、结果消息连接到模型请求。"""
    messages = build_initial_messages(question)
    assistant_message = request_message(api_key, client, messages)

    # 第一次回复只接受一个允许的工具调用；不执行模型生成的任意代码。
    preview = extract_tool_preview(assistant_message)
    tool_call = assistant_message["tool_calls"][0]
    tool_call_id = tool_call.get("id")
    if assistant_message.get("role") != "assistant" or tool_call.get("type") != "function":
        raise ValueError("工具调用消息的角色或类型不正确；未执行查询。")
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("缺少有效的工具调用 ID；未执行查询。")

    result = execute_tool(preview["name"], preview["arguments"])
    no_evidence_answer = answer_when_evidence_is_missing(preview["name"], result)
    if no_evidence_answer is not None:
        return {
            "model": MODEL,
            "question": question,
            "tool_name": preview["name"],
            "arguments_json": preview["arguments"],
            "tool_result": result,
            "answer": no_evidence_answer,
            "model_requests": 1,
        }
    tool_message = build_tool_message(tool_call_id, result)

    # 先保留模型自己的调用请求，再加入对应的工具执行结果。
    messages.append(assistant_message)
    messages.append(tool_message)

    # 第二次只让模型根据已有结果回答；不再提供工具，不进入无限循环。
    final_message = request_message(api_key, client, messages, offer_tools=False)
    if final_message.get("role") != "assistant" or final_message.get("tool_calls"):
        raise ValueError("第二次回复不是最终回答；已停止，不再执行工具或追加请求。")
    answer = final_message.get("content")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("模型没有返回非空文字回答；本次流程未完成。")
    if preview["name"] == "search_knowledge_base":
        answer = append_verified_citations(answer, result)
    return {
        "model": MODEL,
        "question": question,
        "tool_name": preview["name"],
        "arguments_json": preview["arguments"],
        "tool_result": result,
        "answer": answer,
        "model_requests": 2,
    }


def main() -> int:
    try:
        api_key = load_api_key()
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), trust_env=False) as client:
            trace = run_roundtrip(api_key, client)
    except (ValidationError, json.JSONDecodeError):
        print("模型生成的工具参数未通过校验；未执行查询，也不会重试。")
        return 1
    except httpx.HTTPError:
        print("模型请求遇到网络或超时错误；流程未完成，不自动重试。")
        return 1
    except ValueError as error:
        print(str(error))
        return 1
    print("真实模型与本地模拟业务数据的闭环结果：")
    print(json.dumps(trace, ensure_ascii=False, indent=2).replace(api_key, "[REDACTED]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
