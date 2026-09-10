"""高风险操作的内存确认仓库；教学阶段只在单进程中使用。"""

from collections.abc import Callable
from threading import Lock
from uuid import uuid4

from action_models import PendingAction
from ticket_repository import TicketRepository
from tickets import InMemoryTicketRepository


class ActionNotFoundError(LookupError):
    pass


class TicketNotFoundError(LookupError):
    pass


class ActionStateError(ValueError):
    """操作存在，但它的当前状态不允许请求的状态转换。"""


class PendingActionStore:
    """先记录操作意图，收到确认后最多执行一次。"""

    def __init__(
        self,
        id_factory: Callable[[], str] | None = None,
        ticket_repository: TicketRepository | None = None,
    ):
        self._actions: dict[str, PendingAction] = {}
        self._lock = Lock()
        self._id_factory = id_factory or (lambda: f"act_{uuid4().hex}")
        self._ticket_repository = ticket_repository or InMemoryTicketRepository()

    def propose_priority_change(self, ticket_id: str, new_priority: str) -> PendingAction:
        ticket = self._ticket_repository.get_by_id(ticket_id)
        if ticket is None:
            raise TicketNotFoundError("工单不存在，未创建待确认操作。")

        action = PendingAction(
            action_id=self._id_factory(),
            tool_name="change_ticket_priority",
            ticket_id=ticket_id,
            previous_priority=ticket["priority"],
            new_priority=new_priority,
            status="pending",
        )
        with self._lock:
            if action.action_id in self._actions:
                raise RuntimeError("待确认操作 ID 冲突，未覆盖已有操作。")
            self._actions[action.action_id] = action
        return action.model_copy(deep=True)

    def get(self, action_id: str) -> PendingAction:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                raise ActionNotFoundError("待确认操作不存在。")
            return action.model_copy(deep=True)

    def confirm(self, action_id: str) -> PendingAction:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                raise ActionNotFoundError("待确认操作不存在。")

            # 相同 action_id 重复确认时直接返回，避免重复执行写操作。
            if action.status == "executed":
                return action.model_copy(deep=True)
            if action.status == "cancelled":
                # 取消必须在底层也生效，防止调用方绕过 AgentRun 直接确认 action_id。
                raise ActionStateError("待确认操作已取消，不能再执行。")

            ticket = self._ticket_repository.change_priority(
                action.ticket_id,
                action.new_priority,
            )
            if ticket is None:
                raise TicketNotFoundError("工单已不存在，操作未执行。")

            executed = action.model_copy(update={"status": "executed"})
            self._actions[action_id] = executed
            return executed.model_copy(deep=True)

    def cancel(self, action_id: str) -> PendingAction:
        """取消尚未执行的操作；重复取消是幂等的，不会产生第二个状态版本。"""
        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                raise ActionNotFoundError("待确认操作不存在。")
            if action.status == "cancelled":
                return action.model_copy(deep=True)
            if action.status == "executed":
                raise ActionStateError("待确认操作已执行，不能再取消。")

            cancelled = action.model_copy(update={"status": "cancelled"})
            self._actions[action_id] = cancelled
            return cancelled.model_copy(deep=True)


ACTION_STORE = PendingActionStore()
