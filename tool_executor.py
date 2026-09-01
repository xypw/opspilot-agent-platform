"""按允许的工具名称分发查询；本文件独立运行时只使用本地模拟数据。"""

import json

from knowledge_base import search_knowledge_base
from orders import query_order
from pending_actions import ACTION_STORE
from tickets import query_ticket
from tool_args import (
    QueryOrderArgs,
    QueryTicketArgs,
    RequestPriorityChangeArgs,
    SearchKnowledgeBaseArgs,
)


def execute_tool(tool_name: str, arguments_json: str) -> dict | list[dict] | None:
    """解析参数，按工具选择对应的校验模型，再执行查询。"""
    args = json.loads(arguments_json)

    if tool_name == "query_ticket":
        validated_args = QueryTicketArgs.model_validate(args)
        return query_ticket(validated_args.ticket_id)

    elif tool_name == "query_order":
        # 订单参数必须用订单模型校验，不能复用要求 ticket_id 的工单模型。
        validated_args = QueryOrderArgs.model_validate(args)
        return query_order(validated_args.order_id)

    elif tool_name == "search_knowledge_base":
        validated_args = SearchKnowledgeBaseArgs.model_validate(args)
        return search_knowledge_base(validated_args.query, validated_args.limit)

    elif tool_name == "request_priority_change":
        validated_args = RequestPriorityChangeArgs.model_validate(args)
        action = ACTION_STORE.propose_priority_change(
            validated_args.ticket_id, validated_args.new_priority
        )
        return action.model_dump()

    else:
        raise ValueError("不支持的工具")


if __name__ == "__main__":
    print("本地分发示例；未调用模型：")
    print(execute_tool("query_ticket", '{"ticket_id": "T-1003"}'))
    print(execute_tool("query_order", '{"order_id": "O-2003"}'))
