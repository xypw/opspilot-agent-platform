"""获批后的小批本地政策验收：Redis硬上限6次，报告独占创建，不调用云端。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

import httpx
import redis

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.smoke_customer_portal import create_database_connection,customer_username

SCOPE="opspilot-local-policy-fix-20261022-v1"


def valid_policy_result(result):
    """验证公开AgentGraphResponse，不依赖未公开的内部rag_evidence状态字段。"""
    raw=result.get("result") or {}
    answer=raw.get("answer","")
    evidence=raw.get("tool_result")
    return (result.get("status")=="COMPLETED" and raw.get("tool_name")=="search_knowledge_base"
            and isinstance(evidence,list) and bool(evidence) and "来源：" in answer
            and all(isinstance(item,dict) and isinstance(item.get("content"),str)
                    and bool(item["content"].strip()) and item["content"] in answer
                    and f"第{item.get('page')}页" in "".join(answer.split()) for item in evidence))


def run(attempt):
    expected={"provider":"ollama","scope":SCOPE,"limit":"6","window":"86400"}
    configured=json.loads(subprocess.check_output(["docker","exec","opspilot-api","python","-c",
        "import os,json; print(json.dumps({'provider':os.getenv('OPSPILOT_MODEL_PROVIDER'),"
        "'scope':os.getenv('MODEL_RATE_LIMIT_SCOPE'),'limit':os.getenv('MODEL_RATE_LIMIT_REQUESTS'),"
        "'window':os.getenv('MODEL_RATE_LIMIT_WINDOW_SECONDS')}))"],text=True))
    if configured!=expected:raise RuntimeError("没有部署本批次本地硬预算，拒绝调用")
    quota=redis.Redis(host="127.0.0.1",port=6380,socket_timeout=3)
    key="opspilot:model-quota:v1:"+hashlib.sha256(SCOPE.encode()).hexdigest()
    used=int(quota.get(key) or 0)
    if used>=6:raise RuntimeError("本批6次已用完")
    target=ROOT/f"reports/local-policy-fix-20261022-attempt-{attempt}.json"
    with target.open("x",encoding="utf-8") as report_file:
        report={"provider":"ollama","budget":6,"question":"退款多久到账","passed":False,
                "cloud_http_attempts":0,"scope":"单例冒烟，不是任务成功率评测"}
        cid=None;marker="policy-fix-"+uuid4().hex
        credentials={}
        for line in (ROOT/"output/customer-login-access.txt").read_text(encoding="utf-8").splitlines():
            if ": " in line:
                name,password=line.split(": ",1);credentials[name]=password
        username=customer_username("U-1001",credentials)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8011",timeout=240,trust_env=False) as client:
                def request(method,path,**kwargs):
                    headers={"X-CSRF-Token":client.cookies.get("opspilot_csrf") or ""} if method!="GET" else {}
                    return client.request(method,path,headers=headers,**kwargs)
                response=request("POST","/auth/login",json={"username":username,"password":credentials[username]})
                if response.status_code!=200:raise RuntimeError("本机登录失败，未发送模型问题")
                try:
                    runtime=request("GET","/conversations/runtime").json()
                    if runtime.get("location")!="local":raise RuntimeError("不是本地模型，停止")
                    created=request("POST","/conversations",json={"title":marker,"conversation_id":str(uuid4())})
                    created.raise_for_status();cid=created.json()["id"]
                    started=perf_counter()
                    response=request("POST","/conversations/"+cid+"/turns",json={
                        "request_id":str(uuid4()),"message":report["question"],"mode":"live"})
                    result=response.json()
                    conversation=request("GET","/conversations/"+cid).json()
                    report.update(http_status=response.status_code,duration_ms=round((perf_counter()-started)*1000),
                        status=result.get("status"),answer=(result.get("result") or {}).get("answer",""),
                        error=result.get("detail"),turn_errors=[t.get("error_code") for t in conversation["turns"]])
                    raw=result.get("result") or {}
                    report["public_result"]=raw
                    report["passed"]=response.status_code==200 and valid_policy_result(result)
                finally:
                    request("POST","/auth/logout")
        finally:
            if cid:
                with create_database_connection() as db:
                    db.execute("DELETE FROM portal_conversations WHERE id=%s AND owner_user_id='U-1001' AND title=%s",(cid,marker))
            report["batch_admissions"]=int(quota.get(key) or 0)
            json.dump(report,report_file,ensure_ascii=False,indent=2)
            print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-local-batch",action="store_true",required=True)
    parser.add_argument("--attempt",type=int,choices=(1,2),default=1)
    options=parser.parse_args()
    if not run(options.attempt)["passed"]:raise SystemExit(1)
