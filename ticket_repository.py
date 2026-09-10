"""OpsPilot 工单仓库：把 PostgreSQL SQL 与 Agent 业务层隔离。"""

from collections.abc import Callable
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from database import create_database_connection


Ticket = dict[str, str]
ALLOWED_PRIORITIES = frozenset({"low", "medium", "high"})


class TicketRepository(Protocol):
    """待确认操作真正需要的最小工单能力。"""

    def get_by_id(self, ticket_id: str) -> Ticket | None: ...

    def change_priority(self, ticket_id: str, new_priority: str) -> Ticket | None: ...


ConnectionFactory = Callable[[], psycopg.Connection]


class PostgresTicketRepository:
    """通过参数化 SQL 操作 PostgreSQL 中的 tickets 表。"""

    def __init__(self, connection_factory: ConnectionFactory = create_database_connection) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _validate_ticket_id(ticket_id: str) -> str:
        if not isinstance(ticket_id, str) or not ticket_id.strip():
            raise ValueError("ticket_id 必须是非空字符串")
        return ticket_id.strip()

    @staticmethod
    def _validate_priority(new_priority: str) -> str:
        if new_priority not in ALLOWED_PRIORITIES:
            raise ValueError("new_priority 必须是 low、medium 或 high")
        return new_priority

    def get_by_id(self, ticket_id: str) -> Ticket | None:
        """按主键读取工单；不存在时返回 None。"""
        normalized_id = self._validate_ticket_id(ticket_id)
        with self._connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT id, status, priority FROM tickets WHERE id = %s",
                    (normalized_id,),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def change_priority(self, ticket_id: str, new_priority: str) -> Ticket | None:
        """更新优先级并返回更新后快照；不存在时返回 None。"""
        normalized_id = self._validate_ticket_id(ticket_id)
        normalized_priority = self._validate_priority(new_priority)
        with self._connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE tickets
                    SET priority = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, status, priority
                    """,
                    (normalized_priority, normalized_id),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None


class ConfiguredPostgresTicketRepository(PostgresTicketRepository):
    """生产配置版本：每次仓库操作创建短连接，便于后续替换为连接池。"""

    def __init__(self) -> None:
        super().__init__(create_database_connection)
