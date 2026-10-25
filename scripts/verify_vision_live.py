"""仅发送已获授权的两张合成图、3个问题；最多6次HTTP，不使用用户文件。"""

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from model_rate_limit import ModelAdmissionError  # noqa: E402
from preview_tool_call import ModelAPIError  # noqa: E402
from vision_service import ConfiguredVisionGateway, validate_image  # noqa: E402


def synthetic_images():
    first=Image.new("RGB",(320,200),(240,20,20))
    second=Image.new("RGB",(320,200),"white")
    draw=ImageDraw.Draw(second)
    font_path=Path("C:/Windows/Fonts/arialbd.ttf")
    font=ImageFont.truetype(str(font_path),28) if font_path.exists() else ImageFont.load_default(size=28)
    draw.rectangle((0,0,319,50),fill=(20,60,240))
    draw.text((20,65),"DEMO ITEM",font=font,fill="black")
    draw.text((20,105),"CODE B7",font=font,fill="black")
    draw.text((20,145),"TEST ONLY",font=font,fill="black")
    result=[]
    for item in (first,second):
        output=BytesIO();item.save(output,"PNG")
        result.append(validate_image(output.getvalue()))
    return result


def run():
    images=synthetic_images()
    cases=[("color","请描述图片的主要颜色。",[images[0]]),
           ("text","请读出图片里的英文和编号，并说明看不清的内容。",[images[1]]),
           ("comparison","对比这两张图片的颜色，以及是否含有文字。不要推断商品质量。",images)]
    gateway=ConfiguredVisionGateway()
    records=[]; attempts=0
    for case_id, question, selected in cases:
        telemetry={}
        record={"id":case_id,"question":question,"image_sha256":[item["sha256"] for item in selected]}
        try:
            result=gateway(question,selected,[],"live",telemetry=telemetry)
            record.update(status="valid_response",result=result)
        except Exception as error:
            record.update(status="failed",error_type=type(error).__name__)
            if isinstance(error,ModelAPIError):record["http_status"]=error.status_code
        record["http_attempts"]=telemetry.get("http_attempts",0)
        attempts+=record["http_attempts"]
        records.append(record)
        # 双次限流、配置/凭据错误或共享预算故障都停止；不自动换模型/供应商。
        if record["status"]=="failed" and (record.get("http_status") in {400,401,403,429}
                or record["error_type"] in {"ModelAdmissionUnavailable","ModelRateLimitExceeded"}):
            break
        if attempts>=6:break
    report={"model":"glm-4.6v-flash","synthetic_images":2,"questions_planned":3,
            "questions_attempted":len(records),"http_attempts":attempts,"http_budget":6,
            "records":records,"limitations":"Small synthetic transport/vision smoke, not task success or general image accuracy."}
    target=ROOT/"reports/vision-live-smoke-20261021.json"
    if target.exists():
        target=target.with_name("vision-live-smoke-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".json")
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if len(records)==3 and all(row["status"]=="valid_response" for row in records) else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-synthetic-batch",action="store_true",required=True)
    parser.parse_args()
    raise SystemExit(run())
