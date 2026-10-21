"""补齐缺失的本机演示身份配置；已有非空令牌保持不变，不打印任何凭据。"""

import argparse
import json
from pathlib import Path
import secrets
import shutil

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def initialize() -> dict:
    environment = ROOT / ".env"
    if not environment.exists():
        raise ValueError("请先按 .env.example 配置数据库；此脚本不会猜测数据库密码")
    values = dotenv_values(environment, interpolate=False)
    tokens = values.get("OPSPILOT_AUTH_TOKENS", "")
    if tokens and tokens.strip():
        parsed = json.loads(tokens)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("已有身份配置无效，请人工检查；未覆盖配置")
    else:
        parsed = {
            secrets.token_hex(24): {"user_id": "U-1001", "roles": ["customer"]},
            secrets.token_hex(24): {"user_id": "U-1002", "roles": ["customer"]},
            secrets.token_hex(24): {"user_id": "U-admin", "roles": ["knowledge_admin"]},
        }
    backup = ROOT / "output/backups/env-before-auth-initialization.env"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copyfile(environment, backup)
    if not tokens or not tokens.strip():
        set_key(str(environment), "OPSPILOT_AUTH_TOKENS", json.dumps(parsed, separators=(",", ":")))
    if not (values.get("OPSPILOT_INTERNAL_SERVICE_TOKEN") or "").strip():
        set_key(str(environment), "OPSPILOT_INTERNAL_SERVICE_TOKEN", secrets.token_hex(32))
    if not (values.get("OPSPILOT_AUTH_REQUIRED") or "").strip():
        set_key(str(environment), "OPSPILOT_AUTH_REQUIRED", "true")
    access_file = ROOT / "output/local-demo-access.txt"
    access_file.write_text(
        "仅供本机虚构演示使用，请勿上传或发送给第三方。\n"
        + "\n".join(f"{user['user_id']} ({','.join(user.get('roles', []))}): {token}"
                    for token, user in parsed.items()) + "\n",
        encoding="utf-8",
    )
    return {"configured_users": len(parsed), "access_file": "output/local-demo-access.txt",
            "external_model_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-missing", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(initialize()))
