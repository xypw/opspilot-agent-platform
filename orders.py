"""第 3 天：订单查询工具。全部为虚构数据，不调用模型。"""

ORDERS = [
    {"id": "O-2001", "status": "shipped", "product": "机械键盘"},
    {"id": "O-2002", "status": "processing", "product": "无线鼠标"},
    {"id": "O-2003", "status": "cancelled", "product": "显示器"},
]


def query_order(order_id: str) -> dict[str, str] | None:
    """按编号返回整条订单；找不到时返回 None，不修改 ORDERS。"""
    for order in ORDERS:
        if order_id == order["id"]:
            return order
    return None
