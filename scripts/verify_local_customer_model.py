"""本机真实模型小批验收：最多24次Ollama HTTP；只用合成数据，不调用云端。"""
import argparse
from io import BytesIO
import json
import hashlib
from pathlib import Path
import secrets
import sys
import subprocess
from time import perf_counter
from uuid import uuid4

import httpx
import redis
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.smoke_customer_portal import create_database_connection, customer_username


def run(business_only=False):
    reconciliation=None
    saved={}
    ledger=ROOT/"output/local-model-budget-20261022.json"
    if ledger.exists():
        saved=json.loads(ledger.read_text(encoding="utf-8"))
        reconciliation=saved.get("reconciliation")
        if "charged" in saved:
            charged=saved["charged"]
        elif saved["attempt"] == 2:
            # 前两轮各发送一次识图，首轮失败也计入；旧Windows日志不能用来计数。
            charged=2
        else:
            raise RuntimeError("旧预算账本不能核算，停止新增推理")
        attempt=saved["attempt"]+1
    else:
        attempt=1
        charged=0
    ledger.parent.mkdir(exist_ok=True)
    def save_budget():
        ledger.write_text(json.dumps({"charged":charged,"attempt":attempt,"budget":24,
                                      "reconciliation":reconciliation,
                                      **{k:saved[k] for k in ("redis_scope","redis_base") if k in saved}}),encoding="utf-8")
    save_budget()
    quota_client=quota_key=None
    if saved.get("redis_scope"):
        # 部署验收专用覆盖文件使全部本地模型入口共用剩余12次硬上限。
        expected={"scope":saved["redis_scope"],"limit":"12","window":"86400"}
        configured=json.loads(subprocess.check_output(["docker","exec","opspilot-api","python","-c",
            "import os,json; print(json.dumps({'scope':os.getenv('MODEL_RATE_LIMIT_SCOPE'),"
            "'limit':os.getenv('MODEL_RATE_LIMIT_REQUESTS'),'window':os.getenv('MODEL_RATE_LIMIT_WINDOW_SECONDS')}))"],text=True))
        if configured!=expected:raise RuntimeError("部署未启用本批次硬预算，停止验收")
        quota_client=redis.Redis(host="127.0.0.1",port=6380,socket_timeout=3)
        quota_key="opspilot:model-quota:v1:"+hashlib.sha256(saved["redis_scope"].encode()).hexdigest()
    def attempts():
        nonlocal charged
        if quota_client is not None:
            count=int(quota_client.get(quota_key) or 0)
            updated=saved["redis_base"]+count
            if updated<charged:raise RuntimeError("共享预算计数丢失，停止新增推理")
            charged=updated
            save_budget()
        return charged
    def budget(worst):
        used=attempts()
        if used+(min(worst,1) if quota_client is not None else worst)>24:
            raise RuntimeError("剩余本地模型预算不足，停止新增问题")
    credentials={}
    for line in (ROOT/"output/customer-login-access.txt").read_text(encoding="utf-8").splitlines():
        if ": " in line:
            name,password=line.split(": ",1);credentials[name]=password
    username=customer_username("U-1001",credentials)
    cid=order_id=None
    marker="local-model-smoke-"+uuid4().hex
    rows=[]
    with httpx.Client(base_url="http://127.0.0.1:8011",timeout=180,trust_env=False) as client:
        def request(method,path,**kwargs):
            headers=kwargs.pop("headers",{})
            if method!="GET":
                headers["X-CSRF-Token"]=client.cookies.get("opspilot_csrf") or ""
            return client.request(method,path,headers=headers,**kwargs)
        login=request("POST","/auth/login",json={"username":username,"password":credentials[username]})
        if login.status_code!=200:raise RuntimeError("本机登录失败，未发送模型问题")
        try:
            runtime=request("GET","/conversations/runtime").json()
            if runtime.get("location")!="local" or runtime.get("requires_image_consent") is not False:
                raise RuntimeError("部署不是本地模式，停止验收")
            created=request("POST","/conversations",json={"title":marker,"conversation_id":str(uuid4())})
            created.raise_for_status();cid=created.json()["id"]
            path="/conversations/"+cid
            def turn(name,question,worst,check,**extra):
                nonlocal charged
                budget(worst)
                before=attempts();started=perf_counter()
                # 请求前预扣最坏上界。崩溃、超时或没有可信遥测时不退回，防止重复运行超预算。
                if quota_client is None:
                    charged+=worst
                    save_budget()
                response=request("POST",path+"/turns",json={
                    "request_id":str(uuid4()),"mode":"live","message":question,**extra})
                result=response.json()
                observed=(result.get("result") or {}).get("model_http_attempts")
                if quota_client is None and response.status_code==200 and type(observed) is int and 0<=observed<=worst:
                    charged-=worst-observed
                    save_budget()
                used=attempts()-before
                valid=response.status_code==200 and check(result)
                rows.append({"case":name,"http_status":response.status_code,"passed":bool(valid),
                    "local_model_http_attempts":used,"duration_ms":round((perf_counter()-started)*1000),
                    "status":result.get("status"),"answer":(result.get("result") or {}).get("answer","")[:1200],
                    "error":result.get("detail") if response.status_code!=200 else None})
                if attempts()>24:
                    raise RuntimeError("本机并发请求干扰预算，停止验收")
            turn("greeting","你好",0,lambda r:"你好" in r["result"]["answer"] and r["result"]["model_http_attempts"]==0)
            turn("query_instructions","查询订单怎么做",0,lambda r:"订单号" in r["result"]["answer"])
            for color,word in (() if business_only else (("red","红"),("blue","蓝"))):
                image=Image.new("RGB",(128,128),"white")
                ImageDraw.Draw(image).rectangle((16,16,112,112),fill=color)
                output=BytesIO();image.save(output,"PNG")
                uploaded=request("POST",path+"/images",files={"file":("synthetic.png",output.getvalue(),"image/png")})
                uploaded.raise_for_status();iid=uploaded.json()["id"]
                turn("vision_"+color,"图中方块主要是什么颜色？只描述看到的内容。",1,
                     lambda r,word=word:word in r["result"]["answer"] and r["result"]["business_write_executed"] is False,
                     image_ids=[iid])
            with create_database_connection() as db:
                for _ in range(100):
                    candidate="O-"+str(8000+secrets.randbelow(1000))
                    row=db.execute("""INSERT INTO orders(id,owner_user_id,status,product,delivered_at,amount_cents)
                        VALUES (%s,'U-1001','delivered',%s,CURRENT_DATE-1,3555)
                        ON CONFLICT DO NOTHING RETURNING id""",(candidate,marker)).fetchone()
                    if row:order_id=candidate;break
                if order_id is None:raise RuntimeError("无法分配合成订单")
                db.execute("""INSERT INTO order_return_policies(order_id,category,product_condition,
                    exclusion_confirmed_at_purchase,downloaded_or_unsealed,activated,merchant_no_reason_days,policy_version)
                    VALUES (%s,'STANDARD','INTACT',true,false,false,0,'local-model-test')""",(order_id,))
            if not business_only:
                turn("query_order","查询订单 "+order_id+" 的当前状态",11,
                     lambda r:any(t["tool_name"]=="query_order" and (t.get("result") or {}).get("id")==order_id
                                  for t in r["result"].get("tool_trace",[])))
            turn("policy_evidence","退款多久到账",11,
                 lambda r:bool(r["result"].get("tool_trace")) and "来源：" in r["result"]["answer"])
            turn("return_draft","帮我申请退货 "+order_id,11,
                 lambda r:r["status"]=="WAITING_CONFIRMATION" and bool(r["result"].get("return_draft")))
            with create_database_connection() as db:
                zero=db.execute("SELECT COUNT(*) FROM return_applications WHERE order_id=%s",(order_id,)).fetchone()[0]==0
            rows.append({"case":"zero_write_without_confirmation","passed":zero,"local_model_http_attempts":0})
        finally:
            request("POST","/auth/logout")
            with create_database_connection() as db:
                if cid:db.execute("DELETE FROM portal_conversations WHERE id=%s AND owner_user_id='U-1001' AND title=%s",(cid,marker))
                if order_id:
                    owned=db.execute("SELECT id FROM orders WHERE id=%s AND product=%s FOR UPDATE",(order_id,marker)).fetchone()
                    if not owned:raise RuntimeError("合成订单发生变化，未清理其他数据")
                    for table in ("return_applications","return_drafts","order_return_policies"):
                        db.execute(f"DELETE FROM {table} WHERE order_id=%s",(order_id,))
                    db.execute("DELETE FROM orders WHERE id=%s AND product=%s",(order_id,marker))
            report={"provider":"ollama","model":"qwen3-vl:4b","budget":24,"http_attempts":attempts(),
                    "budget_accounting":"累计保守计数；本批剩余额度由Redis共享准入硬限制，含失败和重试，不因重跑清零。",
                    "cloud_model_http_attempts":0,"passed":sum(bool(row["passed"]) for row in rows),
                    "cases":rows,"limits":"小批合成数据，不代表生产准确率；未确认正式业务写入。"}
            target=ROOT/f"reports/local-model-smoke-20261022-attempt-{attempt}.json"
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-local-batch",action="store_true",required=True)
    parser.add_argument("--business-only",action="store_true",help="只复测尚未通过的政策与草稿，避免重发已通过图片")
    options=parser.parse_args()
    result=run(options.business_only)
    if not result["cases"] or not all(row["passed"] for row in result["cases"]):
        raise SystemExit(1)
