"""账号登录：scrypt 密码、随机可撤销会话、HttpOnly Cookie 与 CSRF 检查。"""

import hashlib
import hmac
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from portal_store import fingerprint, portal_store

SESSION_COOKIE = "opspilot_session"
CSRF_COOKIE = "opspilot_csrf"


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码长度须为12至128个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return f"scrypt-v1${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, salt, digest = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(digest) != 64 or len(password) > 128:
            return False
        computed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(computed.hex(), digest)
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = hash_password("dummy-not-a-user-password")


def check_same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (origin is not None and origin != expected
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "不允许跨站登录或修改操作")


def authenticate_session(request: Request):
    from auth import _configured_tokens, AuthenticatedUser
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        raise HTTPException(401, "请先登录，或登录已失效")
    record = portal_store().session(fingerprint(raw))
    if record is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_same_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if len(csrf) > 100 or not hmac.compare_digest(fingerprint(csrf), record["csrf_hash"]):
            raise HTTPException(403, "CSRF校验失败，请刷新页面")
    # 浏览器持随机会话，Java 受控客户端使用服务端已有身份令牌。两者不混用。
    identity = account_identity(record)
    request.state.portal_username = record["username"]
    return AuthenticatedUser(identity.user_id, identity.roles, identity.token)


def account_identity(record):
    from auth import AuthenticatedUser, _configured_tokens
    if record.get('roles') is not None:
        return AuthenticatedUser(record['user_id'], frozenset(record['roles']), 'portal:'+record['user_id'])
    # Compatibility for pre-migration fixtures; deployed accounts are backfilled.
    identity = next((user for user in _configured_tokens().values() if user.user_id == record['user_id']), None)
    if identity is None:
        raise HTTPException(403, '账户业务身份已停用')
    return identity


def establish_session(store, account, request, response):
    identity = account_identity(account)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.create_session(account['username'], fingerprint(token), fingerprint(csrf))
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        store.revoke_session(fingerprint(old))
    secure = os.getenv('PORTAL_SECURE_COOKIES','false').lower() == 'true'
    response.set_cookie(SESSION_COOKIE,token,max_age=28800,httponly=True,secure=secure,samesite='strict',path='/')
    response.set_cookie(CSRF_COOKIE,csrf,max_age=28800,httponly=False,secure=secure,samesite='strict',path='/')
    response.headers['Cache-Control']='no-store'
    return {'username':account['username'],'user_id':identity.user_id,'roles':sorted(identity.roles),
            'demo_order_id':account.get('demo_order_id')}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,40}$")
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=12,max_length=128)


class AccountStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool = Field(strict=True)


def build_auth_router() -> APIRouter:
    from auth import authenticate_request, _configured_tokens
    router = APIRouter(prefix="/auth", tags=["账号登录"])

    @router.post("/login")
    def login(body: LoginRequest, request: Request, response: Response):
        check_same_origin(request)
        store = portal_store()
        username = body.username.lower()
        if not store.admit_login(username, request.client.host if request.client else "unknown"):
            raise HTTPException(429, "登录尝试过于频繁，请稍后重试", headers={"Retry-After": "900"})
        account = store.account(username)
        correct = verify_password(body.password, account["password_hash"] if account else DUMMY_PASSWORD_HASH)
        if not account or not correct:
            raise HTTPException(401, "账号或密码错误")
        return establish_session(store,account,request,response)

    @router.post('/register',status_code=201)
    def register(body: RegisterRequest, request: Request, response: Response):
        check_same_origin(request)
        store=portal_store()
        address=request.client.host if request.client else 'unknown'
        if not store.admit_login('register:'+address,'register:'+address):
            raise HTTPException(429,'注册过于频繁，请15分钟后重试',headers={'Retry-After':'900'})
        account=store.register(body.username.lower(),hash_password(body.password))
        return establish_session(store,account,request,response)

    def administrator(request: Request):
        user=authenticate_session(request)
        from auth import require_role
        require_role(user,'admin')
        return request.state.portal_username

    @router.get('/admin/users')
    def users(offset: int = 0, actor=Depends(administrator)):
        if offset < 0 or offset > 100000:
            raise HTTPException(400,'分页参数无效')
        rows=portal_store().list_accounts(offset)
        return {'items':rows,'next_offset':offset+50 if len(rows)==50 else None}

    @router.patch('/admin/users/{username}')
    def status(username: str, body: AccountStatusRequest, actor=Depends(administrator)):
        try:
            return portal_store().set_account_active(actor,username,body.active)
        except PermissionError:
            raise HTTPException(403,'管理员身份已失效') from None

    @router.get("/me")
    def me(request: Request, response: Response, user=Depends(authenticate_request)):
        from auth import debug_enabled
        response.headers["Cache-Control"] = "no-store"
        return {"username": getattr(request.state, "portal_username", user.user_id),
                "user_id": user.user_id, "roles": sorted(user.roles),
                "debug_access": debug_enabled() and "developer" in user.roles,
                "demo_order_id": (portal_store().account(request.state.portal_username) or {}).get('demo_order_id')
                    if getattr(request.state,'portal_username',None) else None}

    @router.post("/logout")
    def logout(request: Request, response: Response, user=Depends(authenticate_request)):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            portal_store().revoke_session(fingerprint(token))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"logged_out": True}

    return router
