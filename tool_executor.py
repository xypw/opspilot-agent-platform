"""按允许的工具名称分发查询；本文件独立运行时只使用本地模拟数据。"""

import json
from collections.abc import Callable

from knowledge_base import search_knowledge_base
from orders import query_order
from pending_actions import ACTION_STORE
from ticket_repository import TicketRepository
from tickets import InMemoryTicketRepository
from tool_args import (
    CheckReturnEligibilityArgs,
    QueryOrderArgs,
    QueryTicketArgs,
    RequestPriorityChangeArgs,
    SearchKnowledgeBaseArgs,
)


# 独立运行本模块时使用内存仓库；FastAPI 启动后会注入与写操作相同的仓库。
TICKET_REPOSITORY: TicketRepository = InMemoryTicketRepository()


def _query_local_order(order_id: str) -> dict[str, object] | None:
    """默认订单查询，保留旧的离线演示和单元测试能力。"""
    return query_order(order_id)


# Python 中函数可以像普通对象一样被保存和替换。
# 参数是一个订单号字符串，返回订单字典或 None。
ORDER_QUERY: Callable[[str], dict[str, object] | None] = _query_local_order


def _return_eligibility_requires_java(order_id: str) -> dict[str, object] | None:
    """直接运行本模块时拒绝伪造规则；FastAPI 启动时会注入 Java 实现。"""
    raise RuntimeError("退货资格工具需要通过 FastAPI 配置 Java 订单服务")


RETURN_ELIGIBILITY_QUERY: Callable[[str], dict[str, object] | None] = (
    _return_eligibility_requires_java
)


def query_ticket(ticket_id: str) -> dict[str, str] | None:
    """保留稳定的查询入口，同时把真实读取委托给当前运行时仓库。"""
    return TICKET_REPOSITORY.get_by_id(ticket_id)


def execute_tool(tool_name: str, arguments_json: str) -> dict | list[dict] | None:
    """解析参数，按工具选择对应的校验模型，再执行查询。"""
    args = json.loads(arguments_json)

    if tool_name == "query_ticket":
        validated_args = QueryTicketArgs.model_validate(args)
        return query_ticket(validated_args.ticket_id)

    elif tool_name == "query_order":
        # 订单参数必须用订单模型校验，不能复用要求 ticket_id 的工单模型。
        validated_args = QueryOrderArgs.model_validate(args)
        # 调用当前注入的查询实现：默认本地，联调时可以替换成 Java HTTP 客户端。
        return ORDER_QUERY(validated_args.order_id)

    elif tool_name == "check_return_eligibility":
        validated_args = CheckReturnEligibilityArgs.model_validate(args)
        return RETURN_ELIGIBILITY_QUERY(validated_args.order_id)

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
