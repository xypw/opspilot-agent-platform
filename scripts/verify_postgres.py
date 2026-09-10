"""验证本地 PostgreSQL/pgvector 已启动、初始化并可查询。"""

import sys
from pathlib import Path

# 直接运行脚本时，把项目根目录加入导入路径。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import create_database_connection


def main() -> None:
    # 连接由 context manager 自动关闭；只做只读健康检查，不修改业务数据。
    with create_database_connection() as connection:
        with connection.cursor() as cursor:
            # 检查 pgvector 扩展和初始化后的演示工单数量。
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector';"
            )
            extension = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM tickets;")
            ticket_count = cursor.fetchone()[0]

    if extension is None:
        raise RuntimeError("pgvector 扩展未安装。")
    if ticket_count != 3:
        raise RuntimeError(f"演示工单数量异常：{ticket_count}。")
    print("PostgreSQL/pgvector OK; tickets=3")


if __name__ == "__main__":
    main()
