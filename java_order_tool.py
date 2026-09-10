"""把模型生成的工具参数转换成一次受控的 Java 订单查询。"""

import json

from order_service_client import JavaOrderClient
from tool_args import QueryOrderArgs


def query_order_via_java(
    arguments_json: str,
    client: JavaOrderClient,
) -> dict[str, object] | None:
    """解析并校验模型参数，再把合法订单号交给 Java 业务服务。"""
    # 把模型返回的 JSON 字符串解析为 Python 字典。
    args = json.loads(arguments_json)
    # 校验字段、类型并去掉订单号首尾空白。
    validated_args = QueryOrderArgs.model_validate(args)
    # model_validate 返回对象，需要通过 .order_id 取出字符串字段。
    return client.get_by_id(validated_args.order_id)
