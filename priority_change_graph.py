"""使用 LangGraph 编排“申请修改工单优先级 -> 等待确认 -> 执行/取消”。

这个模块只负责工作流顺序，不直接保存 HTTP 请求，也不直接修改数据库。
真正的业务状态仍由 PendingActionStore 管理，这样编排层和业务层职责清晰。
"""

from typing import Literal, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from action_models import PendingAction


Priority = Literal["low", "medium", "high"]
WorkflowStatus = Literal["WAITING_CONFIRMATION", "COMPLETED", "CANCELLED"]


class WorkflowNotFoundError(LookupError):
    """客户端给出的 thread_id 没有任何已保存的 LangGraph 状态。"""


class WorkflowStateError(ValueError):
    """工作流存在，但不处于允许 resume 的等待确认状态。"""


class PriorityActionStore(Protocol):
    """LangGraph 依赖的最小业务接口；内存版和 Redis 版都能实现它。"""

    def propose_priority_change(self, ticket_id: str, new_priority: str) -> PendingAction: ...

    def confirm(self, action_id: str) -> PendingAction: ...

    def cancel(self, action_id: str) -> PendingAction: ...


class PriorityChangeState(TypedDict, total=False):
    """节点之间共享的 Agent 状态，相当于工作流中的数据传输对象。"""

    ticket_id: str
    new_priority: Priority
    action_id: str
    approved: bool
    status: WorkflowStatus
    tool_result: dict[str, str]
    answer: str


class PriorityChangeStartRequest(BaseModel):
    """FastAPI 启动工作流时校验的请求体。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(min_length=1, max_length=100)
    ticket_id: str = Field(min_length=1, max_length=100)
    new_priority: Priority


class PriorityChangeResumeRequest(BaseModel):
    """用户确认或拒绝时传入的恢复数据。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool


class PriorityChangeWorkflowResponse(BaseModel):
    """屏蔽 LangGraph 内部字段，向前端返回稳定的业务响应。"""

    thread_id: str
    ticket_id: str
    new_priority: Priority
    action_id: str
    status: WorkflowStatus
    answer: str | None = None
    tool_result: dict[str, str] | None = None


def _read_approval(resume_value: object) -> bool:
    """把 Command(resume=...) 的外部数据转换成明确的 bool。"""
    if isinstance(resume_value, dict) and isinstance(resume_value.get("approved"), bool):
        return resume_value["approved"]
    raise ValueError("恢复工作流时必须提供布尔类型的 approved。")


def build_priority_change_graph(
    action_store: PriorityActionStore,
    checkpointer: InMemorySaver | None = None,
):
    """构建并编译状态图；Store 通过参数注入，测试时可以使用独立实例。"""

    def propose_node(state: PriorityChangeState) -> PriorityChangeState:
        # 副作用单独放在一个节点中。该节点完成后 LangGraph 会保存 Checkpoint，
        # 后面的 await_approval 恢复时不会重新执行这里，从而避免重复创建申请。
        action = action_store.propose_priority_change(
            state["ticket_id"],
            state["new_priority"],
        )
        return {
            "action_id": action.action_id,
            "status": "WAITING_CONFIRMATION",
            "tool_result": action.model_dump(),
            "answer": f"操作 {action.action_id} 等待确认，尚未修改工单。",
        }

    def await_approval_node(state: PriorityChangeState) -> PriorityChangeState:
        # 第一次运行到这里时 interrupt 会暂停并返回控制权给 FastAPI。
        # 之后 Command(resume=...) 会让节点从头重跑，而 interrupt 会返回恢复数据。
        resume_value = interrupt({
            "action_id": state["action_id"],
            "ticket_id": state["ticket_id"],
            "new_priority": state["new_priority"],
            "message": "该操作会修改工单，是否确认执行？",
        })
        return {"approved": _read_approval(resume_value)}

    def route_after_approval(state: PriorityChangeState) -> Literal["execute", "cancel"]:
        """条件边：根据用户选择决定下一个节点。"""
        return "execute" if state["approved"] else "cancel"

    def execute_node(state: PriorityChangeState) -> PriorityChangeState:
        action = action_store.confirm(state["action_id"])
        return {
            "status": "COMPLETED",
            "tool_result": action.model_dump(),
            "answer": (
                f"已执行操作 {action.action_id}；工单 {action.ticket_id} 的优先级"
                f"已从 {action.previous_priority} 改为 {action.new_priority}。"
            ),
        }

    def cancel_node(state: PriorityChangeState) -> PriorityChangeState:
        action = action_store.cancel(state["action_id"])
        return {
            "status": "CANCELLED",
            "tool_result": action.model_dump(),
            "answer": f"已取消操作 {action.action_id}；工单优先级未修改。",
        }

    builder = StateGraph(PriorityChangeState)
    builder.add_node("propose", propose_node)
    builder.add_node("await_approval", await_approval_node)
    builder.add_node("execute", execute_node)
    builder.add_node("cancel", cancel_node)

    builder.add_edge(START, "propose")
    builder.add_edge("propose", "await_approval")
    builder.add_conditional_edges(
        "await_approval",
        route_after_approval,
        {"execute": "execute", "cancel": "cancel"},
    )
    builder.add_edge("execute", END)
    builder.add_edge("cancel", END)

    # checkpointer 保存每个 thread_id 的执行位置和 State，是 interrupt/resume 的前提。
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def start_priority_change_workflow(
    graph,
    request: PriorityChangeStartRequest,
) -> PriorityChangeWorkflowResponse:
    """从 START 运行到 interrupt，并把内部 State 转换成 API 响应。"""
    config = {"configurable": {"thread_id": request.thread_id}}
    state = graph.invoke(
        {"ticket_id": request.ticket_id, "new_priority": request.new_priority},
        config=config,
    )
    return _to_response(request.thread_id, state)


def resume_priority_change_workflow(
    graph,
    thread_id: str,
    request: PriorityChangeResumeRequest,
) -> PriorityChangeWorkflowResponse:
    """用同一个 thread_id 找回 Checkpoint，并从 interrupt 后继续运行。"""
    config = {"configurable": {"thread_id": thread_id}}

    # Command(resume=...) 本身只携带“用户是否同意”，不携带 ticket_id 等完整 State。
    # 因此必须先检查这个 thread_id 是否真的有暂停的 Checkpoint；否则 LangGraph 会尝试
    # 从 START 执行，节点读取不到 ticket_id，最终表现为不友好的 KeyError/500。
    snapshot = graph.get_state(config)
    if "action_id" not in snapshot.values:
        raise WorkflowNotFoundError("工作流不存在，或该 thread_id 从未启动。")
    if "await_approval" not in snapshot.next:
        raise WorkflowStateError("工作流当前不在等待确认，不能恢复。")

    state = graph.invoke(Command(resume={"approved": request.approved}), config=config)
    return _to_response(thread_id, state)


def _to_response(thread_id: str, state: PriorityChangeState) -> PriorityChangeWorkflowResponse:
    """LangGraph 的 __interrupt__ 等内部字段不属于公开 API 契约。"""
    return PriorityChangeWorkflowResponse(
        thread_id=thread_id,
        ticket_id=state["ticket_id"],
        new_priority=state["new_priority"],
        action_id=state["action_id"],
        status=state["status"],
        answer=state.get("answer"),
        tool_result=state.get("tool_result"),
    )
