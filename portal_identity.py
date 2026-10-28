"""Short-lived portal-to-Java identity, verified with active account in Java.

Domain-separated HMAC key derived from the existing internal service secret.
The resulting token is server-only and never stored in workflow state.
"""
import base64
import hashlib
import hmac
import json
import time


def issue_business_identity(user_id: str) -> str:
    from auth import _internal_service_token
    secret = _internal_service_token()
    if len(secret) < 16:
        raise RuntimeError('内部服务凭据未配置')
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    now = int(time.time())
    payload = encode(json.dumps({'sub':user_id,'iat':now,'exp':now+30,
                                 'iss':'opspilot-portal','aud':'opspilot-business'},separators=(',',':')).encode())
    data = header+'.'+payload
    key = hmac.new(secret.encode(),b'opspilot-portal-identity-v1',hashlib.sha256).digest()
    signature = encode(hmac.new(key,data.encode(),hashlib.sha256).digest())
    return data+'.'+signature
