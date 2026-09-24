"""本机跨服务冒烟：需先在 127.0.0.1:8081 启动 Java 内存服务。"""
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["OPSPILOT_AUTH_REQUIRED"] = "false"

from order_query_factory import build_order_query, build_return_eligibility_query
from return_draft_client import JavaReturnDraftGateway
from service_api import build_service_router

url = "http://127.0.0.1:8081"
app = FastAPI()
app.include_router(build_service_router(
    order_query=build_order_query("java", url),
    eligibility_query=build_return_eligibility_query(url),
    draft_gateway=JavaReturnDraftGateway(url),
    evidence_reviewer=lambda _question, _candidates: {},
))
with TestClient(app) as client:
    order = client.get("/service/orders/O-2001")
    assert order.status_code == 200, (order.status_code, order.text)
    eligibility = client.get("/service/orders/O-2001/return-eligibility")
    assert eligibility.status_code == 200, (eligibility.status_code, eligibility.text)
    draft_response = client.post("/service/orders/O-2001/return-drafts")
    assert draft_response.status_code == 200, (draft_response.status_code, draft_response.text)
    draft = draft_response.json()
    path = f"/service/orders/O-2001/return-drafts/{draft['draft_id']}"
    reread = client.get(path)
    assert reread.status_code == 200, (reread.status_code, reread.text)
    confirmation = {
        "product": draft["product"],
        "amount_cents": draft["amount_cents"],
        "expires_at": draft["expires_at"],
    }
    stale = client.post(path + "/confirm", json={**confirmation, "amount_cents": 1})
    assert stale.status_code == 409, (stale.status_code, stale.text)
    accepted = client.post(path + "/confirm", json=confirmation)
    assert accepted.status_code == 200, (accepted.status_code, accepted.text)
    assert accepted.json()["status"] == "SUBMITTED", accepted.json()
    print({
        "order": order.status_code,
        "eligibility": eligibility.json().get("decision"),
        "draft": draft["status"],
        "stale_confirmation": stale.status_code,
        "accepted_confirmation": accepted.json()["status"],
    })
