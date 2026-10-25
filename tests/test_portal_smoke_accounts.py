"""账号改名后的验收脚本回归；不读取本机密码或连接真实数据库。"""
import unittest
from unittest.mock import MagicMock, patch

from scripts.smoke_customer_portal import customer_username, create_database_connection


class SmokeAccountTests(unittest.TestCase):
    def test_localhost_uses_bound_ipv4_with_timeout(self):
        with patch("scripts.smoke_customer_portal.load_database_url", return_value="postgresql://localhost/demo"), patch(
                "scripts.smoke_customer_portal.psycopg.connect") as connect:
            create_database_connection()
        connect.assert_called_once_with("postgresql://localhost/demo", connect_timeout=5, hostaddr="127.0.0.1")

    def test_explicit_ipv6_or_remote_host_is_not_rewritten(self):
        for url in ("postgresql://[::1]/demo", "postgresql://database.internal/demo"):
            with patch("scripts.smoke_customer_portal.load_database_url", return_value=url), patch(
                    "scripts.smoke_customer_portal.psycopg.connect") as connect:
                create_database_connection()
            connect.assert_called_once_with(url, connect_timeout=5)

    def test_resolves_username_by_business_identity(self):
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = ("renamed-user",)
        with patch("scripts.smoke_customer_portal.create_database_connection", return_value=connection):
            self.assertEqual(customer_username("U-1001", {"renamed-user":"test-only"}),
                             "renamed-user")
        connection.__enter__.return_value.execute.assert_called_once_with(
            "SELECT username FROM portal_accounts WHERE user_id=%s AND active", ("U-1001",))

    def test_missing_credentials_fail_without_echoing_password(self):
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = ("renamed-user",)
        with patch("scripts.smoke_customer_portal.create_database_connection", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "凭据缺失"):
                customer_username("U-1001", {"old-user":"secret-not-to-print"})
