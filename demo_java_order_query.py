"""真实 HTTP 联调：Python -> 本机 Java；使用虚构订单，模型请求次数为 0。

先启动 business-service，再从 OpsPilot 目录运行本文件。
这是客户端演示，尚未替换 /chat 或 tool_executor 的默认内存订单工具。
"""

import json

import httpx

import tool_executor
from order_service_client import JavaOrderClient, OrderServiceError
from tool_executor import execute_tool


def main() -> int:
    # 固定可信地址与超时；不读取系统代理、不跟随重定向到其他地址。
    with httpx.Client(
        base_url="http://127.0.0.1:8081",
        timeout=2.0,
        trust_env=False,
        follow_redirects=False,
    ) as http:
        client = JavaOrderClient(http)
        # 保存默认依赖，避免演示结束后影响同一进程中的其他查询。
        previous_order_query = tool_executor.ORDER_QUERY
        # 把 Java 客户端方法注入正式的 Agent 工具分发器。
        tool_executor.ORDER_QUERY = client.get_by_id
        try:
            for order_id in ("O-2001", "O-2003", "O-9999"):
                arguments_json = json.dumps({"order_id": order_id})
                result = execute_tool("query_order", arguments_json)
                print(json.dumps({"requested_id": order_id, "order": result}, ensure_ascii=False))
        except OrderServiceError as error:
            print(f"联调未通过：{error}")
            return 1
        finally:
            # 即使请求失败也恢复默认查询，防止全局依赖残留。
            tool_executor.ORDER_QUERY = previous_order_query
    print("真实 Java HTTP 查询完成；数据为模拟数据；模型请求次数：0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
