"""高风险操作的内存确认仓库；教学阶段只在单进程中使用。"""

from collections.abc import Callable
from threading import Lock
from uuid import uuid4

from action_models import PendingAction
from tickets import change_ticket_priority, query_ticket


class ActionNotFoundError(LookupError):
    pass


class TicketNotFoundError(LookupError):
    pass


class PendingActionStore:
    """先记录操作意图，收到确认后最多执行一次。"""

    def __init__(self, id_factory: Callable[[], str] | None = None):
        self._actions: dict[str, PendingAction] = {}
        self._lock = Lock()
        self._id_factory = id_factory or (lambda: f"act_{uuid4().hex}")

    def propose_priority_change(self, ticket_id: str, new_priority: str) -> PendingAction:
        ticket = query_ticket(ticket_id)
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

            ticket = change_ticket_priority(action.ticket_id, action.new_priority)
            if ticket is None:
                raise TicketNotFoundError("工单已不存在，操作未执行。")

            executed = action.model_copy(update={"status": "executed"})
            self._actions[action_id] = executed
            return executed.model_copy(deep=True)


ACTION_STORE = PendingActionStore()
