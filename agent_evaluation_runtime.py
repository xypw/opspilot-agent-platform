"""Agent 评测运行环境：提供不依赖 Docker 的可重复离线基线。"""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid5

from agent_graph import ConfiguredAgentModelGateway, build_agent_graph
from knowledge_base import search_knowledge_base
from orders import query_order
from pending_actions import PendingActionStore
from tickets import InMemoryTicketRepository
from tool_executor import ConfiguredToolExecutor


# 冻结评测时间，避免同一条订单用例随着真实日期变化而从“七天内”变成“已过期”。
EVALUATION_DATE = date(2026, 9, 7)
# 固定命名空间让相同订单得到稳定但互不冲突的草稿 UUID。
EVALUATION_DRAFT_NAMESPACE = UUID("9e57a394-39cc-4e2c-a265-0d2519122401")


class OfflineEvaluationDraftGateway:
    """只实现当前离线评测需要的草稿创建，不请求 Java 服务。"""

    def start(self, order_id: str) -> dict:
        # 资格工具已经验证订单存在，这里再次读取是为了构造真实形状的业务快照。
        order = query_order(order_id)
        if order is None:
            raise ValueError("评测订单不存在。")
        # 固定起止时间保证每次运行结果一致，且满足一小时草稿契约。
        started_at = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
        return {
            "draft_id": str(uuid5(EVALUATION_DRAFT_NAMESPACE, order_id)),
            "order_id": order_id,
            "product": order["product"],
            "amount_cents": order["amount_cents"],
            "started_at": started_at.isoformat(),
            "expires_at": (started_at + timedelta(hours=1)).isoformat(),
            "status": "WAITING_REASON",
        }


def query_offline_return_eligibility(order_id: str) -> dict | None:
    """按冻结日期计算退货资格，规则与 Java 业务层保持同一语义。"""
    order = query_order(order_id)
    if order is None:
        return None
    if order["status"] != "delivered" or order["delivered_at"] is None:
        return {"order_id": order_id, "decision": "NOT_ALLOWED", "can_apply": False}

    delivered_at = date.fromisoformat(order["delivered_at"])
    days_since_delivery = (EVALUATION_DATE - delivered_at).days
    if days_since_delivery < 0:
        decision = "NOT_ALLOWED"
    elif days_since_delivery <= 7:
        decision = "NO_REASON_ALLOWED"
    elif days_since_delivery <= 15:
        decision = "REASON_REQUIRED"
    else:
        decision = "NOT_ALLOWED"
    return {
        "order_id": order_id,
        "decision": decision,
        "can_apply": decision != "NOT_ALLOWED",
    }


def build_isolated_evaluation_graph():
    """构建使用本地固定数据的 Agent 图，不连接 Redis、数据库或 Java。"""
    # 查询和写操作共享同一个内存工单仓库，避免同一进程读写两个不同数据源。
    ticket_repository = InMemoryTicketRepository()
    action_store = PendingActionStore(ticket_repository=ticket_repository)
    # 每张图持有自己的工具依赖，不修改模块级全局变量，也不会污染其他测试。
    tool_runner = ConfiguredToolExecutor(
        ticket_repository=ticket_repository,
        order_query=query_order,
        return_eligibility_query=query_offline_return_eligibility,
        knowledge_search=search_knowledge_base,
        action_store=action_store,
    )
    # 默认 Checkpointer 是内存版；模型网关的 mock 模式不会读取 API Key。
    return build_agent_graph(
        action_store,
        ConfiguredAgentModelGateway(),
        draft_gateway=OfflineEvaluationDraftGateway(),
        tool_runner=tool_runner.execute,
    )
