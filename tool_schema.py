"""发给模型的工具说明，不是函数实现，也不包含本地业务数据。"""

QUERY_TICKET_TOOL = {
    "type": "function",
    "function": {
        "name": "query_ticket",
        "description": "按工单编号查询工单的状态和优先级；这是只读查询。",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "用户提供的工单编号，例如 T-1001；不要编造编号。",
                }
            },
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
    },
}


QUERY_ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "query_order",
        "description": "按订单编号查询购买的商品及订单状态；不用于查询工单，这是只读查询。",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "用户提供的订单编号，例如 O-2001；不要编造编号。",
                }
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
}


REQUEST_PRIORITY_CHANGE_TOOL = {
    "type": "function",
    "function": {
        "name": "request_priority_change",
        "description": (
            "申请修改工单优先级。此工具只创建待用户确认的操作，不会立即修改工单；"
            "用户明确要求修改时使用，不用于只读查询。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "用户提供的工单编号，例如 T-1003；不要编造编号。",
                },
                "new_priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "用户要求的新优先级。",
                },
            },
            "required": ["ticket_id", "new_priority"],
            "additionalProperties": False,
        },
    },
}


SEARCH_KNOWLEDGE_BASE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "搜索企业制度和操作指南，返回带标题、页码和原文的证据片段；"
            "用于回答政策、流程和规范问题，不用于查询具体订单或工单。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "根据用户问题提炼的检索词或简短问题。",
                    "minLength": 2,
                    "maxLength": 200,
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回的证据片段数，默认 3。",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


# Agent 可选择业务查询、知识检索或创建待确认操作；每轮只执行一个工具。
TOOLS = [
    QUERY_TICKET_TOOL,
    QUERY_ORDER_TOOL,
    SEARCH_KNOWLEDGE_BASE_TOOL,
    REQUEST_PRIORITY_CHANGE_TOOL,
]
