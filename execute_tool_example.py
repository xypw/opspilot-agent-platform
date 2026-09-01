"""本地参数解析练习：只使用固定示例，不读取密钥、不请求模型。

先解析 JSON，再校验并规范化编号，最后查询。
本文件保留为第 2 天的单工具练习；agent_roundtrip.py 现使用 tool_executor.py 分发两种查询。
"""

import json

from tickets import query_ticket
from tool_args import QueryTicketArgs


def execute_query_example(arguments_json: str) -> dict[str, str] | None:
    """本地教学流程：解析、校验、查询；校验失败时不执行查询。"""
    args = json.loads(arguments_json)
    validated_args = QueryTicketArgs.model_validate(args)
    result = query_ticket(validated_args.ticket_id)
    return result


if __name__ == "__main__":
    # 参数文本与上一次模型返回相同，但本次是固定本地示例，不是实时模型响应。
    arguments_json = '{"ticket_id":"T-1003"}'
    print("本地固定参数示例；未调用模型：")
    print(execute_query_example(arguments_json))
