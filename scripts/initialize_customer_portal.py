"""非破坏性初始化登录/历史表及演示账号；密码只写入被忽略的本机文件。"""

import argparse
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import _configured_tokens  # noqa: E402
from database import create_database_connection  # noqa: E402
from portal_auth import hash_password  # noqa: E402
from portal_store import PostgresPortalStore  # noqa: E402


def initialize():
    with create_database_connection() as connection:
        connection.execute((ROOT / "db/migrations/003_customer_portal.sql").read_text(encoding="utf-8"))
        connection.execute((ROOT / "db/migrations/004_conversation_memory.sql").read_text(encoding="utf-8"))
        connection.execute((ROOT / "db/migrations/005_portal_registration.sql").read_text(encoding="utf-8"))
    store = PostgresPortalStore()
    created = []
    for username, user_id in (("customer1", "U-1001"), ("customer2", "U-1002"), ("ys10087", "U-admin")):
        if not any(user.user_id == user_id for user in _configured_tokens().values()):
            continue
        if store.account(username):
            continue
        password = secrets.token_urlsafe(18)
        if store.create_account(username, user_id, hash_password(password)):
            created.append(f"{username}: {password}")
    if store.account('ys10087'):
        from scripts.upgrade_portal_accounts import upgrade
        upgrade()
    if created:
        target = ROOT / "output/customer-login-access.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as output:
            output.write("本机虚构演示账号；不要上传、截图分享或提交Git。\n"+"\n".join(created)+"\n")
    return {"created_accounts": len(created), "passwords_printed": False,
            "access_file": "output/customer-login-access.txt", "external_model_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-missing", action="store_true", required=True)
    parser.parse_args()
    print(initialize())
