"""Redis 版 Agent Checkpoint 与待确认操作仓库。

内存版本适合单进程教学，但进程重启就会丢失数据。本模块把状态序列化成 JSON
存入 Redis；Key 的命名空间让运行状态、幂等索引和待确认操作不会互相混淆。
"""

from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

import redis

from action_models import PendingAction
from checkpoint_models import AgentRunCheckpoint
from checkpoint_store import IdempotencyConflictError, RunNotFoundError, RunStateError
from pending_actions import ActionNotFoundError, ActionStateError, TicketNotFoundError
from ticket_repository import TicketRepository
from tickets import InMemoryTicketRepository


class RedisClient(Protocol):
    """本模块实际用到的 Redis 客户端最小接口，便于离线测试替换成 FakeRedis。"""

    def get(self, name: str) -> str | bytes | None: ...
    def set(self, name: str, value: str, nx: bool = False) -> bool | None: ...
    def pipeline(self, transaction: bool = True): ...
    def lock(self, name: str, timeout: int, blocking_timeout: int): ...


def create_redis_client(redis_url: str) -> redis.Redis:
    """创建并检查 Redis 连接；调用方在应用启动阶段决定是否启用它。"""
    if not isinstance(redis_url, str) or not redis_url.strip():
        raise ValueError("redis_url 必须是非空字符串")
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.ping()
    return client


def _as_text(value: str | bytes | None) -> str | None:
    """redis-py 可以返回 str 或 bytes，统一后再交给 Pydantic 解析 JSON。"""
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else value


class RedisPendingActionStore:
    """把 PendingAction 存在 Redis，且用每个 action 的锁保护状态转换。"""

    def __init__(
        self,
        client: RedisClient,
        *,
        namespace: str = "opspilot",
        id_factory: Callable[[], str] | None = None,
        ticket_repository: TicketRepository | None = None,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._id_factory = id_factory or (lambda: f"act_{uuid4().hex}")
        self._ticket_repository = ticket_repository or InMemoryTicketRepository()

    def _action_key(self, action_id: str) -> str:
        return f"{self._namespace}:pending_action:{action_id}"

    def _lock_key(self, action_id: str) -> str:
        return f"{self._namespace}:lock:pending_action:{action_id}"

    def _save(self, action: PendingAction, *, nx: bool = False) -> bool:
        saved = self._client.set(self._action_key(action.action_id), action.model_dump_json(), nx=nx)
        # redis-py 的 set 在 nx=False 时通常返回 True；这里显式转换，避免依赖具体实现。
        return bool(saved)

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
        # NX 防止极小概率的 ID 冲突覆盖另一个用户的待确认操作。
        if not self._save(action, nx=True):
            raise RuntimeError("待确认操作 ID 冲突，未覆盖已有操作。")
        return action.model_copy(deep=True)

    def get(self, action_id: str) -> PendingAction:
        raw = _as_text(self._client.get(self._action_key(action_id)))
        if raw is None:
            raise ActionNotFoundError("待确认操作不存在。")
        return PendingAction.model_validate_json(raw)

    def confirm(self, action_id: str) -> PendingAction:
        """确认时在 Redis 锁内检查并更新，重复确认不会重复写工单。"""
        with self._client.lock(self._lock_key(action_id), timeout=10, blocking_timeout=2):
            action = self.get(action_id)
            if action.status == "executed":
                return action
            if action.status == "cancelled":
                raise ActionStateError("待确认操作已取消，不能再执行。")

            ticket = self._ticket_repository.change_priority(
                action.ticket_id,
                action.new_priority,
            )
            if ticket is None:
                raise TicketNotFoundError("工单已不存在，操作未执行。")
            executed = action.model_copy(update={"status": "executed"})
            self._save(executed)
            return executed.model_copy(deep=True)

    def cancel(self, action_id: str) -> PendingAction:
        """取消与确认共享同一把 action 锁，避免两个请求同时改变状态。"""
        with self._client.lock(self._lock_key(action_id), timeout=10, blocking_timeout=2):
            action = self.get(action_id)
            if action.status == "cancelled":
                return action
            if action.status == "executed":
                raise ActionStateError("待确认操作已执行，不能再取消。")
            cancelled = action.model_copy(update={"status": "cancelled"})
            self._save(cancelled)
            return cancelled.model_copy(deep=True)


class RedisAgentRunStore:
    """把 AgentRunCheckpoint 和幂等索引存入 Redis。"""

    def __init__(
        self,
        client: RedisClient,
        action_store: RedisPendingActionStore,
        *,
        namespace: str = "opspilot",
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._action_store = action_store
        self._namespace = namespace
        self._id_factory = id_factory or (lambda: f"run_{uuid4().hex}")

    def _run_key(self, run_id: str) -> str:
        return f"{self._namespace}:agent_run:{run_id}"

    def _idempotency_key(self, thread_id: str, idempotency_key: str) -> str:
        return f"{self._namespace}:agent_run:idempotency:{thread_id}:{idempotency_key}"

    def _start_lock_key(self, thread_id: str, idempotency_key: str) -> str:
        return f"{self._namespace}:lock:agent_run:start:{thread_id}:{idempotency_key}"

    def _run_lock_key(self, run_id: str) -> str:
        return f"{self._namespace}:lock:agent_run:{run_id}"

    def _save(self, run: AgentRunCheckpoint) -> None:
        self._client.set(self._run_key(run.run_id), run.model_dump_json())

    def get(self, run_id: str) -> AgentRunCheckpoint:
        raw = _as_text(self._client.get(self._run_key(run_id)))
        if raw is None:
            raise RunNotFoundError("Agent 运行不存在。")
        return AgentRunCheckpoint.model_validate_json(raw)

    def start(
        self,
        thread_id: str,
        idempotency_key: str,
        message: str,
        chat_runner: Callable[[str], dict],
    ) -> AgentRunCheckpoint:
        """用幂等键锁住“查旧运行 → 调模型 → 保存新运行”的完整临界区。"""
        with self._client.lock(
            self._start_lock_key(thread_id, idempotency_key), timeout=30, blocking_timeout=5
        ):
            index_key = self._idempotency_key(thread_id, idempotency_key)
            existing_run_id = _as_text(self._client.get(index_key))
            if existing_run_id is not None:
                existing = self.get(existing_run_id)
                if existing.user_message != message:
                    raise IdempotencyConflictError("同一个 idempotency_key 已用于不同消息；拒绝覆盖原运行。")
                return existing

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
                if not isinstance(action_id, str) or tool_result.get("status") != "pending":
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
            # Redis 事务让 run 正文和“幂等键 → run_id”索引一起可见。
            pipeline = self._client.pipeline(transaction=True)
            pipeline.set(self._run_key(run.run_id), run.model_dump_json())
            pipeline.set(index_key, run.run_id)
            pipeline.execute()
            return run.model_copy(deep=True)

    def confirm(self, run_id: str) -> AgentRunCheckpoint:
        with self._client.lock(self._run_lock_key(run_id), timeout=10, blocking_timeout=2):
            run = self.get(run_id)
            if run.status == "COMPLETED":
                if run.pending_action_id is not None:
                    return run
                raise RunStateError("该 Agent 运行没有待确认操作。")
            if run.status == "CANCELLED":
                raise RunStateError("该 Agent 运行已取消，不能再确认。")
            if run.pending_action_id is None:
                raise RunStateError("Checkpoint 状态与待确认操作不一致。")

            action = self._action_store.confirm(run.pending_action_id)
            completed = run.model_copy(update={
                "status": "COMPLETED",
                "tool_result": action.model_dump(),
                "answer": (
                    f"[模拟恢复] 已确认操作 {action.action_id}；工单 {action.ticket_id} "
                    f"的优先级已从 {action.previous_priority} 改为 {action.new_priority}。"
                ),
                "version": run.version + 1,
            })
            self._save(completed)
            return completed.model_copy(deep=True)

    def cancel(self, run_id: str) -> AgentRunCheckpoint:
        with self._client.lock(self._run_lock_key(run_id), timeout=10, blocking_timeout=2):
            run = self.get(run_id)
            if run.status == "CANCELLED":
                return run
            if run.status == "COMPLETED":
                raise RunStateError("已完成的 Agent 运行不能取消。")
            if run.pending_action_id is None:
                raise RunStateError("Checkpoint 状态与待确认操作不一致。")

            action = self._action_store.cancel(run.pending_action_id)
            cancelled = run.model_copy(update={
                "status": "CANCELLED",
                "tool_result": action.model_dump(),
                "answer": f"已取消操作 {action.action_id}；工单优先级未修改。",
                "version": run.version + 1,
            })
            self._save(cancelled)
            return cancelled.model_copy(deep=True)
