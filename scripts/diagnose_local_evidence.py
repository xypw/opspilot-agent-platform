"""授权24次批次内的单次合成证据诊断，不使用云端或真实资料。"""
import json
from pathlib import Path
import sys
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evidence_review import EvidenceVerdict,build_evidence_review_messages

def run(root=ROOT):
    target=root/"reports/local-model-evidence-diagnostic-20261022.json"
    # x模式先原子占用报告名；并发重复执行也不能重复发出请求。
    with target.open("x",encoding="utf-8") as report_file:
        report={"status":"NOT_SENT","http_attempts":0,"scope":"单次合成政策诊断，不是评测"}
        try:
            ledger=root/"output/local-model-budget-20261022.json"
            saved=json.loads(ledger.read_text(encoding="utf-8"))
            if saved.get("redis_scope"):
                raise RuntimeError("本批已使用共享硬预算，请通过部署验收入口，不能绕过准入")
            charged=saved.get("charged")
            if type(charged) is not int or not 0<=charged<24:
                raise RuntimeError("本批预算不足或账本无效")
            saved["charged"]=charged+1
            ledger.write_text(json.dumps(saved,ensure_ascii=False),encoding="utf-8")
            messages=build_evidence_review_messages("退款多久到账",[{
                "chunk_id":"synthetic-policy-1","content":"退款审核通过后，款项将在1至3个工作日原路退回。"}])
            report["http_attempts"]=1
            with httpx.Client(timeout=90,trust_env=False) as client:
                response=client.post("http://127.0.0.1:11434/api/chat",json={
                    "model":"qwen3-vl:4b","messages":messages,"stream":False,"think":False,
                    "format":EvidenceVerdict.model_json_schema(),
                    "options":{"temperature":0.1,"num_ctx":8192,"num_predict":2048}})
            response.raise_for_status()
            result=response.json();message=result.get("message",{})
            report.update(status="RECEIVED",http_status=response.status_code,done_reason=result.get("done_reason"),
                content=message.get("content"),thinking_length=len(message.get("thinking","")),eval_count=result.get("eval_count"))
            return report
        except Exception as error:
            report["error_type"]=type(error).__name__
            raise
        finally:
            json.dump(report,report_file,ensure_ascii=False,indent=2)


if __name__=="__main__":
    if sys.argv[1:]!=["--approved-local-batch"]:
        raise SystemExit("须先批准本地合成数据调用预算")
    print(json.dumps(run(),ensure_ascii=False,indent=2))
