"""OpsPilot 的 PostgreSQL 连接入口。

本模块只管理连接与配置，不掺入工单、订单或检索业务逻辑。这样业务仓库可在
内存教学版与 PostgreSQL 实现之间替换，FastAPI 和 Agent 不需要知道细节。
"""

import os
from pathlib import Path

import psycopg
from dotenv import dotenv_values


class DatabaseConfigurationError(RuntimeError):
    """数据库地址缺失或格式不合法时，阻止服务误连到未知数据库。"""


def load_database_url() -> str:
    """优先读环境变量，其次读本地 .env；绝不打印可能含密码的 URL。"""
    values = dotenv_values(Path(__file__).with_name(".env"), interpolate=False)
    return (os.getenv("DATABASE_URL") or values.get("DATABASE_URL") or "").strip()


def create_database_connection(database_url: str | None = None) -> psycopg.Connection:
    """创建 PostgreSQL 连接；调用方负责用 with 块关闭连接。"""
    url = load_database_url() if database_url is None else database_url.strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise DatabaseConfigurationError("DATABASE_URL 必须是 PostgreSQL 连接地址。")
    return psycopg.connect(url)
