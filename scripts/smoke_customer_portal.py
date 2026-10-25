"""本机部署验收：真实Cookie/HTTP/PG/Redis/Java，视觉和Agent均用mock，不外发图片。"""

from io import BytesIO
import argparse
import json
from pathlib import Path
import secrets
import sys
import subprocess
import time
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
import psycopg
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import load_database_url  # noqa: E402


def create_database_connection():
    # Compose仅映射IPv4回环，避免Windows对localhost先尝试未绑定的IPv6。
    url = load_database_url()
    options = {"connect_timeout": 5}
    if urlsplit(url).hostname == "localhost":
        options["hostaddr"] = "127.0.0.1"
    return psycopg.connect(url, **options)


def customer_username(user_id, credentials):
    """按稳定的业务身份查账号，账号改名后不依赖旧用户名。"""
    with create_database_connection() as db:
        row = db.execute("SELECT username FROM portal_accounts WHERE user_id=%s AND active",
                         (user_id,)).fetchone()
    if row is None or row[0] not in credentials:
        raise RuntimeError("演示用户或本机凭据缺失，请重新配置账号文件")
    return row[0]


def verify_external_consent(runtime, rejected_request, check):
    """mock冒烟不能因切换到本地模式而意外调用模型。"""
    if runtime.get("location")=="cloud" and runtime.get("requires_image_consent") is True:
        rejected_request()
        check("live_image_requires_consent",True)
    elif runtime.get("location")=="local" and runtime.get("requires_image_consent") is False:
        check("local_consent_probe_skipped_no_model_call",True)
    else:
        raise RuntimeError("模型运行位置与授权配置不一致，停止验收")


def run(verify_restart=False):
    # 不打印密码、Cookie或Authorization；此文件本身不会包含真实凭据。
    credentials = {}
    for line in (ROOT/"output/customer-login-access.txt").read_text(encoding="utf-8").splitlines():
        if ": " in line:
            username, password = line.split(": ", 1)
            credentials[username] = password
    primary = customer_username("U-1001", credentials)
    secondary = customer_username("U-1002", credentials)
    checks, conversation_ids, order_id = [], [], None
    marker = "portal-local-smoke-"+uuid4().hex

    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)

    def request(client, method, path, expected=200, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if method != "GET" and client.cookies.get("opspilot_csrf"):
            headers["X-CSRF-Token"] = client.cookies.get("opspilot_csrf")
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: expected {expected}, got {response.status_code}; {response.text[:300]}")
        return response.json() if "json" in response.headers.get("content-type", "") else response

    def payload(message, **extra):
        return {"request_id":str(uuid4()), "message":message, "mode":"mock", **extra}

    try:
        with httpx.Client(base_url="http://127.0.0.1:8011", timeout=90, trust_env=False) as customer, httpx.Client(
            base_url="http://127.0.0.1:8011", timeout=30, trust_env=False) as other:
            check("portal_page", 'id="login-form"' in request(customer,"GET","/demo").text)
            request(customer,"POST","/auth/login",json={"username":primary,"password":credentials[primary]})
            check("cookie_login_identity", request(customer,"GET","/auth/me")["user_id"]=="U-1001")
            no_csrf = customer.post("/conversations", json={})
            check("cookie_csrf_required", no_csrf.status_code==403)
            request(customer,"POST","/conversations",expected=403,headers={"Origin":"https://not-our-origin.invalid"},json={})
            check("cross_origin_blocked", True)
            creation = {"title":marker,"conversation_id":str(uuid4())}
            conversation = request(customer,"POST","/conversations",json=creation)
            cid = str(conversation["id"]); conversation_ids.append(cid)
            repeated_creation=request(customer,"POST","/conversations",json=creation)
            check("conversation_creation_retry_same_id", repeated_creation["id"]==cid)
            check("empty_conversation_hidden_from_history",
                  all(item["id"]!=cid for item in request(customer,"GET","/conversations")["items"]))
            path = "/conversations/"+cid
            question = payload("查询订单 O-7101")
            first = request(customer,"POST",path+"/turns",json=question)
            repeated = request(customer,"POST",path+"/turns",json=question)
            check("same_request_same_turn", first["id"]==repeated["id"])
            check("text_agent_mock_completed", first["status"]=="COMPLETED" and first["result"]["model_http_attempts"]==0)
            followup = request(customer,"POST",path+"/turns",json=payload("这笔订单当前状态是什么"))
            check("followup_requeries_order", any(step["tool_name"]=="query_order" for step in followup["result"]["tool_trace"]))
            check("visible_history_persisted", len(request(customer,"GET",path)["turns"])==2)
            request(other,"POST","/auth/login",json={"username":secondary,"password":credentials[secondary]})
            request(other,"GET",path,expected=404)
            check("foreign_history_hidden", all(item["id"]!=cid for item in request(other,"GET","/conversations")["items"]))
            output=BytesIO();Image.new("RGB",(48,32),"red").save(output,"PNG")
            uploaded = request(customer,"POST",path+"/images",files={"file":("synthetic.png",output.getvalue(),"image/png")})
            image_id = uploaded["id"]
            check("private_image_readable", request(customer,"GET",path+"/images/"+image_id).status_code==200)
            request(other,"GET",path+"/images/"+image_id,expected=404)
            check("foreign_image_blocked",True)
            vision = request(customer,"POST",path+"/turns",json=payload("图片中有什么？不要执行业务操作",image_ids=[image_id]))
            check("mock_vision_explicitly_labelled", vision["kind"]=="vision" and vision["result"]["model"]=="mock-vision")
            check("image_qa_has_no_write", vision["result"]["business_write_executed"] is False)
            request(customer,"POST",path+"/turns/"+vision["id"]+"/actions",expected=409,json={"action":"confirm"})
            check("image_qa_cannot_confirm",True)
            reused = request(customer,"POST",path+"/turns",json=payload("继续观察上一张图",reuse_images=True))
            check("image_followup_uses_original_attachment", reused["image_ids"]==[image_id])
            # 只对本轮合成会话注入中断状态，不杀进程、不调用外部模型。
            with create_database_connection() as db:
                owned = db.execute("""SELECT id FROM portal_conversations
                    WHERE id=%s AND owner_user_id='U-1001' AND title=%s FOR UPDATE""", (cid,marker)).fetchone()
                if not owned:
                    raise RuntimeError("合成会话发生变化，停止故障注入")
                db.execute("""UPDATE portal_turns SET status='RUNNING',result=NULL,updated_at=NOW()
                    WHERE id=%s AND conversation_id=%s AND kind='vision'""", (reused["id"],cid))
                db.execute("UPDATE portal_conversations SET active_turn_id=%s WHERE id=%s", (reused["id"],cid))
            sync_path=path+"/turns/"+reused["id"]+"/sync"
            request(customer,"POST",sync_path,expected=409)
            check("recent_vision_not_interrupted",True)
            with create_database_connection() as db:
                db.execute("""UPDATE portal_turns SET updated_at=NOW()-INTERVAL '16 minutes'
                    WHERE id=%s AND conversation_id=%s AND kind='vision'""", (reused["id"],cid))
            recovered=request(customer,"POST",sync_path)
            check("stale_vision_recovers_without_replay", recovered["status"]=="FAILED"
                  and recovered["error_code"]=="VISION_INTERRUPTED"
                  and recovered["result"]["business_write_executed"] is False)
            check("vision_recovery_releases_conversation", request(customer,"GET",path)["active_turn_id"] is None)
            policy = request(customer,"POST",path+"/turns",json=payload("结合图片和政策说明退款多久到账",image_ids=[image_id],use_policy=True,policy_query="退款多久到账"))
            check("image_plus_rag_has_separate_evidence", policy["result"]["policy"]["status"]=="SUPPORTED" and bool(policy["result"]["policy"]["evidence"]))
            runtime=request(customer,"GET","/conversations/runtime")
            verify_external_consent(runtime,lambda:request(customer,"POST",path+"/turns",expected=400,
                json={**payload("看图",image_ids=[image_id]),"mode":"live"}),check)
            if verify_restart:
                restarted=subprocess.run(["docker","compose","restart","api"],cwd=ROOT,
                                         capture_output=True,text=True,timeout=45)
                if restarted.returncode:raise RuntimeError("API重启失败，停止验收")
                for _ in range(60):
                    try:
                        if customer.get("/health").status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(1)
                else:raise RuntimeError("API重启后未恢复健康")
                check("login_session_survives_api_process_restart",request(customer,"GET","/auth/me")["user_id"]=="U-1001")
                check("history_survives_api_process_restart",len(request(customer,"GET",path)["turns"])==5)
                check("image_survives_api_process_restart",request(customer,"GET",path+"/images/"+image_id).status_code==200)
            with create_database_connection() as db:
                for _ in range(100):
                    candidate="O-"+str(8000+secrets.randbelow(1000))
                    inserted=db.execute("""INSERT INTO orders(id,owner_user_id,status,product,delivered_at,amount_cents)
                        VALUES (%s,'U-1001','delivered',%s,CURRENT_DATE-1,3555)
                        ON CONFLICT DO NOTHING RETURNING id""",(candidate,marker)).fetchone()
                    if inserted: order_id=candidate;break
                if order_id is None:raise RuntimeError("无法分配合成订单")
                db.execute("""INSERT INTO order_return_policies(order_id,category,product_condition,
                    exclusion_confirmed_at_purchase,downloaded_or_unsealed,activated,merchant_no_reason_days,policy_version)
                    VALUES (%s,'STANDARD','INTACT',true,false,false,0,'portal-smoke-v1')""",(order_id,))
            application = request(customer,"POST",path+"/turns",json=payload("帮我申请退货 "+order_id))
            check("agent_stops_for_approval",application["status"]=="WAITING_CONFIRMATION")
            with create_database_connection() as db:
                count=db.execute("SELECT COUNT(*) FROM return_applications WHERE order_id=%s",(order_id,)).fetchone()[0]
            check("zero_write_before_approval",count==0)
            action_path=path+"/turns/"+application["id"]+"/actions"
            request(customer,"POST",action_path,expected=409,json={"action":"confirm","expected_draft_id":"old-snapshot"})
            check("approval_binds_displayed_draft",True)
            body={"action":"confirm","expected_draft_id":application["result"]["return_draft"]["draft_id"]}
            approved=request(customer,"POST",action_path,json=body)
            duplicate=request(customer,"POST",action_path,json=body)
            check("duplicate_confirmation_same_application", approved["result"]["return_application"]["application_id"]==duplicate["result"]["return_application"]["application_id"])
            with create_database_connection() as db:
                count=db.execute("SELECT COUNT(*) FROM return_applications WHERE order_id=%s",(order_id,)).fetchone()[0]
            check("one_business_write",count==1)
            old_cookie=customer.cookies.get("opspilot_session")
            request(customer,"POST","/auth/logout")
            request(customer,"GET","/auth/me",expected=401,headers={"Cookie":"opspilot_session="+old_cookie})
            check("logout_revokes_old_session",True)
            request(customer,"POST","/auth/login",json={"username":primary,"password":credentials[primary]})
            check("history_survives_relogin",len(request(customer,"GET",path)["turns"])==6)
            request(customer,"POST","/auth/logout"); request(other,"POST","/auth/logout")
        return {"checks_passed":len(checks),"checks":checks,"external_model_calls":0,
                "vision_mode":"mock","real_image_quality_evaluated":False}
    finally:
        with create_database_connection() as db:
            for cid in conversation_ids:
                db.execute("DELETE FROM portal_conversations WHERE id=%s AND owner_user_id='U-1001' AND title=%s",(cid,marker))
            if order_id:
                row=db.execute("SELECT id FROM orders WHERE id=%s AND product=%s FOR UPDATE",(order_id,marker)).fetchone()
                if not row:raise RuntimeError("合成订单发生外部变化，停止清理")
                for table in ("return_applications","return_drafts","order_return_policies"):
                    # table是源码固定白名单，不接受用户输入。
                    db.execute(f"DELETE FROM {table} WHERE order_id=%s",(order_id,))
                db.execute("DELETE FROM orders WHERE id=%s AND product=%s",(order_id,marker))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-restart",action="store_true")
    args=parser.parse_args()
    result=run(verify_restart=args.verify_restart)
    target=ROOT/"output/customer-portal-smoke.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
