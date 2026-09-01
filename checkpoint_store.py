"""线程安全的内存 Checkpoint 仓库；用于演示 Interrupt / Resume。"""

from collections.abc import Callable
from threading import Lock
from uuid import uuid4

from checkpoint_models import AgentRunCheckpoint
from pending_actions import ACTION_STORE, PendingActionStore


class RunNotFoundError(LookupError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class RunStateError(ValueError):
    pass


class AgentRunStore:
    def __init__(
        self,
        action_store: PendingActionStore,
        id_factory: Callable[[], str] | None = None,
    ):
        self._action_store = action_store
        self._id_factory = id_factory or (lambda: f"run_{uuid4().hex}")
        self._runs: dict[str, AgentRunCheckpoint] = {}
        self._idempotency_index: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    def start(
        self,
        thread_id: str,
        idempotency_key: str,
        message: str,
        chat_runner: Callable[[str], dict],
    ) -> AgentRunCheckpoint:
        """相同会话与幂等键只运行一次；重试直接返回原 Checkpoint。"""
        key = (thread_id, idempotency_key)
        with self._lock:
            existing_run_id = self._idempotency_index.get(key)
            if existing_run_id is not None:
                existing = self._runs[existing_run_id]
                if existing.user_message != message:
                    raise IdempotencyConflictError(
                        "同一个 idempotency_key 已用于不同消息；拒绝覆盖原运行。"
                    )
                return existing.model_copy(deep=True)

            # 在建立索引前完成 Agent 调用；失败时不会留下半个 Checkpoint。
            chat_result = chat_runner(message)
            tool_name = chat_result.get("tool_name")
            tool_result = chat_result.get("tool_result")
            answer = chat_result.get("answer")
            if not isinstance(tool_name, str) or not isinstance(answer, str) or not answer.strip():
                raise ValueError("Agent 返回结果不符合 Checkpoint 契约。")

            pending_action_id = None
            status = "COMPLETED"
            if tool_name == "request_priority_change":
                if not isinstance(tool_result, dict):
                    raise ValueError("修改申请缺少待确认操作结果。")
                action_id = tool_result.get("action_id")
                action_status = tool_result.get("status")
                if not isinstance(action_id, str) or action_status != "pending":
                    raise ValueError("修改申请没有返回有效的 pending action。")
                pending_action_id = action_id
                status = "WAITING_CONFIRMATION"

            run = AgentRunCheckpoint(
                run_id=self._id_factory(),
                thread_id=thread_id,
                idempotency_key=idempotency_key,
                status=status,
                user_message=message,
                pending_action_id=pending_action_id,
                tool_name=tool_name,
                tool_result=tool_result,
                answer=answer,
                version=1,
                model_requests=chat_result.get("model_requests", 0),
                simulated_model_requests=chat_result.get("simulated_model_requests", 0),
            )
            if run.run_id in self._runs:
                raise RuntimeError("Agent run_id 冲突，未覆盖已有运行。")
            self._runs[run.run_id] = run
            self._idempotency_index[key] = run.run_id
            return run.model_copy(deep=True)

    def get(self, run_id: str) -> AgentRunCheckpoint:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFoundError("Agent 运行不存在。")
            return run.model_copy(deep=True)

    def confirm(self, run_id: str) -> AgentRunCheckpoint:
        """从 WAITING_CONFIRMATION 恢复；重复确认已完成的写操作是幂等的。"""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFoundError("Agent 运行不存在。")

            if run.status == "COMPLETED":
                if run.pending_action_id is not None:
                    return run.model_copy(deep=True)
                raise RunStateError("该 Agent 运行没有待确认操作。")
            if run.pending_action_id is None:
                raise RunStateError("Checkpoint 状态与待确认操作不一致。")

            action = self._action_store.confirm(run.pending_action_id)
            answer = (
                f"[模拟恢复] 已确认操作 {action.action_id}；工单 {action.ticket_id} "
                f"的优先级已从 {action.previous_priority} 改为 {action.new_priority}。"
            )
            completed = run.model_copy(update={
                "status": "COMPLETED",
                "tool_result": action.model_dump(),
                "answer": answer,
                "version": run.version + 1,
            })
            self._runs[run_id] = completed
            return completed.model_copy(deep=True)


RUN_STORE = AgentRunStore(ACTION_STORE)
