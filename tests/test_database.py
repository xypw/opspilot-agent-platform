"""PostgreSQL 连接配置测试：不依赖本机 Docker 服务。"""

import os
import unittest
from unittest.mock import patch

from database import (
    DatabaseConfigurationError,
    create_database_connection,
    load_database_url,
)


class DatabaseTests(unittest.TestCase):
    def test_environment_variable_has_priority_over_dotenv(self):
        with patch("database.dotenv_values", return_value={"DATABASE_URL": "postgresql://file"}):
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://environment"}):
                self.assertEqual(load_database_url(), "postgresql://environment")

    def test_invalid_database_url_is_rejected_before_connection(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "PostgreSQL"):
            create_database_connection("mysql://not-allowed")

    def test_connection_uses_explicit_postgresql_url(self):
        with patch("database.psycopg.connect", return_value="fake-connection") as connect:
            connection = create_database_connection("postgresql://user:password@host/database")

        self.assertEqual(connection, "fake-connection")
        connect.assert_called_once_with("postgresql://user:password@host/database")


if __name__ == "__main__":
    unittest.main()
