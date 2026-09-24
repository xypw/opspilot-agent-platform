"""HTTP 身份边界：Bearer Token 只在服务端配置，不进入 Prompt 或 Agent State。"""

from __future__ import annotations

import hmac
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    roles: frozenset[str]
    token: str = field(repr=False)


_current_user: ContextVar[AuthenticatedUser | None] = ContextVar(
    "opspilot_current_user", default=None
)


def _local_values() -> dict:
    return dotenv_values(Path(__file__).with_name(".env"), interpolate=False)


def auth_required() -> bool:
    raw = os.getenv("OPSPILOT_AUTH_REQUIRED")
    if raw is None:
        raw = _local_values().get("OPSPILOT_AUTH_REQUIRED", "true")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _configured_tokens() -> dict[str, AuthenticatedUser]:
    raw = os.getenv("OPSPILOT_AUTH_TOKENS")
    if raw is None:
        raw = _local_values().get("OPSPILOT_AUTH_TOKENS", "")
    if not str(raw).strip():
        return {}
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError as error:
        raise RuntimeError("OPSPILOT_AUTH_TOKENS 必须是合法 JSON。") from error
    if not isinstance(data, dict):
        raise RuntimeError("OPSPILOT_AUTH_TOKENS 必须是 token 到用户信息的对象。")

    result: dict[str, AuthenticatedUser] = {}
    for token, details in data.items():
        if not isinstance(token, str) or len(token) < 16:
            raise RuntimeError("每个访问令牌至少需要 16 个字符。")
        if not isinstance(details, dict):
            raise RuntimeError("访问令牌对应的用户信息必须是对象。")
        user_id = details.get("user_id")
        roles = details.get("roles", [])
        if not isinstance(user_id, str) or not user_id.strip():
            raise RuntimeError("每个访问令牌必须配置非空 user_id。")
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise RuntimeError("roles 必须是字符串列表。")
        result[token] = AuthenticatedUser(user_id.strip(), frozenset(roles), token)
    return result


def _internal_service_token() -> str:
    raw = os.getenv("OPSPILOT_INTERNAL_SERVICE_TOKEN")
    if raw is None:
        raw = _local_values().get("OPSPILOT_INTERNAL_SERVICE_TOKEN", "")
    return str(raw).strip()


def authenticate_request(request: Request) -> AuthenticatedUser:
    """验证调用者；启用鉴权但未配置令牌时 fail closed。"""
    if not auth_required():
        return AuthenticatedUser(
            "local-demo-user", frozenset({"customer", "knowledge_admin"}), ""
        )
    try:
        tokens = _configured_tokens()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="服务端鉴权配置无效。") from None
    if not tokens or len(_internal_service_token()) < 16:
        raise HTTPException(status_code=503, detail="服务端鉴权配置不完整。")
    scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise HTTPException(status_code=401, detail="缺少 Bearer 访问令牌。")
    for configured, user in tokens.items():
        if hmac.compare_digest(credential, configured):
            return user
    raise HTTPException(status_code=401, detail="访问令牌无效。")


def require_role(user: AuthenticatedUser, role: str) -> None:
    if role not in user.roles:
        raise HTTPException(status_code=403, detail="当前用户没有执行该操作的权限。")


@contextmanager
def bind_authenticated_user(user: AuthenticatedUser) -> Iterator[None]:
    context_token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(context_token)


def current_authorization_header() -> dict[str, str]:
    """只供受控 Java 客户端使用；用户令牌与内部令牌均不进入模型或状态。"""
    user = _current_user.get()
    if user is None or not user.token:
        return {}
    internal_token = _internal_service_token()
    if len(internal_token) < 16:
        raise RuntimeError("启用鉴权时必须配置至少 16 个字符的内部服务令牌。")
    return {
        "Authorization": f"Bearer {user.token}",
        "X-OpsPilot-Service-Token": internal_token,
    }
