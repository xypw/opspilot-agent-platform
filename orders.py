"""第 3 天：订单查询工具。全部为虚构数据，不调用模型。"""

ORDERS = [
    {
        "id": "O-2001", "status": "delivered", "product": "机械键盘",
        "delivered_at": "2026-08-31", "amount_cents": 39900,
    },
    {
        "id": "O-2002", "status": "processing", "product": "无线鼠标",
        "delivered_at": None, "amount_cents": 12900,
    },
    {
        "id": "O-2003", "status": "cancelled", "product": "显示器",
        "delivered_at": None, "amount_cents": 159900,
    },
]


def query_order(order_id: str) -> dict[str, object] | None:
    """按编号返回整条订单；找不到时返回 None，不修改 ORDERS。"""
    for order in ORDERS:
        if order_id == order["id"]:
            return order
    return None
