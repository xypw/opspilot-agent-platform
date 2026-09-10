"""模拟工单查询：这里不依赖 FastAPI，也不调用大模型。"""

from ticket_repository import Ticket, TicketRepository

TICKETS = [
    {"id": "T-1001", "status": "open", "priority": "high"},
    {"id": "T-1002", "status": "closed", "priority": "low"},
    {"id": "T-1003", "status": "open", "priority": "medium"},
]


def query_ticket(ticket_id: str) -> dict[str, str] | None:
    """按编号返回整条工单；所有工单都不匹配时返回 None。"""
    for ticket in TICKETS:
        if ticket["id"] == ticket_id:
            return ticket
    return None


def change_ticket_priority(ticket_id: str, new_priority: str) -> dict[str, str] | None:
    """修改工单优先级并返回快照；只能由确认服务调用，不能直接暴露给模型。"""
    for ticket in TICKETS:
        if ticket["id"] == ticket_id:
            ticket["priority"] = new_priority
            return ticket.copy()
    return None


class InMemoryTicketRepository(TicketRepository):
    """用现有 TICKETS 列表实现仓库接口，供离线教学和单元测试使用。"""

    def get_by_id(self, ticket_id: str) -> Ticket | None:
        return query_ticket(ticket_id)

    def change_priority(self, ticket_id: str, new_priority: str) -> Ticket | None:
        return change_ticket_priority(ticket_id, new_priority)
