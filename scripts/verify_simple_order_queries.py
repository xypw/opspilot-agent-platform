"""本机查询分流验收：真实登录/Java/PG，明确查询不调用模型，不创建申请。"""
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.smoke_customer_portal import create_database_connection,customer_username


def run():
    credentials={}
    for line in (ROOT/"output/customer-login-access.txt").read_text(encoding="utf-8").splitlines():
        if ": " in line:
            name,password=line.split(": ",1);credentials[name]=password
    username=customer_username("U-1001",credentials)
    with create_database_connection() as db:
        existing=db.execute("SELECT id FROM orders WHERE owner_user_id='U-1001' ORDER BY id LIMIT 1").fetchone()
        foreign=db.execute("SELECT id FROM orders WHERE owner_user_id<>'U-1001' ORDER BY id LIMIT 1").fetchone()
        missing=next((f"O-{n}" for n in range(9999,9900,-1) if db.execute(
            "SELECT id FROM orders WHERE id=%s",(f"O-{n}",)).fetchone() is None),None)
    if not existing or not missing:raise RuntimeError("缺少本机虚构订单测试条件，未发送请求")
    marker="simple-order-check-"+uuid4().hex
    cases=[("incomplete","查询订单101","完整编号格式"),
           ("short_prefixed","查询订单O-101","完整编号格式"),
           ("missing","查询订单"+missing,"未找到你可访问的订单"),
           ("existing","查询订单"+existing[0],"订单金额")]
    if foreign:cases.append(("foreign","查询订单"+foreign[0],"未找到你可访问的订单"))
    rows=[];cid=None
    with httpx.Client(base_url="http://127.0.0.1:8011",timeout=10,trust_env=False) as client:
        def request(method,path,**kwargs):
            headers={"X-CSRF-Token":client.cookies.get("opspilot_csrf") or ""} if method!="GET" else {}
            return client.request(method,path,headers=headers,**kwargs)
        login=request("POST","/auth/login",json={"username":username,"password":credentials[username]})
        if login.status_code!=200:raise RuntimeError("本机演示登录失败")
        try:
            response=request("POST","/conversations",json={"title":marker,"conversation_id":str(uuid4())})
            response.raise_for_status();cid=response.json()["id"]
            for name,question,expected in cases:
                started=perf_counter()
                response=request("POST","/conversations/"+cid+"/turns",json={
                    "message":question,"request_id":str(uuid4()),"mode":"live"})
                raw=response.json();result=raw.get("result") or {}
                passed=(response.status_code==200 and raw.get("status")=="COMPLETED"
                        and expected in result.get("answer","") and result.get("model_http_attempts")==0
                        and not result.get("return_draft") and not result.get("return_application"))
                rows.append({"case":name,"http_status":response.status_code,"passed":passed,
                    "duration_ms":round((perf_counter()-started)*1000),"answer":result.get("answer"),
                    "model_http_attempts":result.get("model_http_attempts")})
        finally:
            request("POST","/auth/logout")
            if cid:
                with create_database_connection() as db:
                    db.execute("DELETE FROM portal_conversations WHERE id=%s AND owner_user_id='U-1001' AND title=%s",(cid,marker))
    report={"cases":rows,"passed":sum(bool(r["passed"]) for r in rows),"model_calls":0,
            "scope":"明确单订单只读分流，不代表自然语言Agent或模型效果"}
    target=ROOT/"reports/simple-order-check-20261022.json"
    with target.open("x",encoding="utf-8") as output:json.dump(report,output,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return len(rows)>0 and all(row["passed"] for row in rows)


if __name__=="__main__":
    if not run():raise SystemExit(1)
