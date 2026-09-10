"""根据 REDIS_URL 选择 OpsPilot 的状态仓库实现。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from checkpoint_store import AgentRunStore, RUN_STORE
from pending_actions import ACTION_STORE, PendingActionStore
from redis_checkpoint_store import (
    RedisAgentRunStore,
    RedisPendingActionStore,
    create_redis_client,
)
from ticket_repository import ConfiguredPostgresTicketRepository, TicketRepository
from tickets import InMemoryTicketRepository


@dataclass(frozen=True)
class RuntimeStores:
    """同一运行环境必须共享 action_store 与 run_store，避免状态写到两个后端。"""

    action_store: PendingActionStore | RedisPendingActionStore
    run_store: AgentRunStore | RedisAgentRunStore
    ticket_repository: TicketRepository
    redis_enabled: bool


def load_redis_url() -> str:
    """环境变量优先，其次读项目 .env；不打印 URL，避免未来 URL 包含认证信息时泄露。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    return (os.getenv("REDIS_URL") or values.get("REDIS_URL") or "").strip()


def load_ticket_repository_backend() -> str:
    """读取工单存储后端；默认 memory，避免测试意外连接外部数据库。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    return (
        os.getenv("TICKET_REPOSITORY_BACKEND")
        or values.get("TICKET_REPOSITORY_BACKEND")
        or "memory"
    ).strip().lower()


def build_ticket_repository(backend: str) -> TicketRepository:
    """根据明确配置选择仓库；拼写错误时快速失败，禁止假持久化。"""
    normalized_backend = backend.strip().lower()
    if normalized_backend == "postgres":
        return ConfiguredPostgresTicketRepository()
    if normalized_backend == "memory":
        return InMemoryTicketRepository()
    raise ValueError("TICKET_REPOSITORY_BACKEND 只能是 memory 或 postgres。")


def build_runtime_stores(
    redis_url: str | None = None,
    ticket_repository: TicketRepository | None = None,
    ticket_backend: str | None = None,
) -> RuntimeStores:
    """未配置 Redis 使用教学内存版；显式配置后连接失败应阻止应用启动。"""
    url = load_redis_url() if redis_url is None else redis_url.strip()
    backend = load_ticket_repository_backend() if ticket_backend is None else ticket_backend
    repository = ticket_repository or build_ticket_repository(backend)
    if not url:
        action_store = PendingActionStore(ticket_repository=repository)
        run_store = AgentRunStore(action_store)
        return RuntimeStores(action_store, run_store, repository, redis_enabled=False)

    client = create_redis_client(url)
    action_store = RedisPendingActionStore(client, ticket_repository=repository)
    run_store = RedisAgentRunStore(client, action_store)
    return RuntimeStores(action_store, run_store, repository, redis_enabled=True)
