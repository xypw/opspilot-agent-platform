"""把查询结果包装成工具消息；本模块不读取密钥、不请求模型。"""

import json

from execute_tool_example import execute_query_example


def build_tool_message(tool_call_id: str, result: dict | list[dict] | None) -> dict[str, str]:
    """使用调用 ID 对应请求，并将结果序列化为 JSON 字符串。"""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, ensure_ascii=False),
    }


if __name__ == "__main__":
    # 固定演示调用 ID，不是平台的真实响应；与工单编号 T-1003 是两回事。
    demo_call_id = "call_demo_001"
    result = execute_query_example('{"ticket_id":"T-1003"}')
    tool_message = build_tool_message(demo_call_id, result)
    print("本地消息构造示例；未调用模型，尚未回传结果。")
    print(json.dumps(tool_message, ensure_ascii=False, indent=2))
