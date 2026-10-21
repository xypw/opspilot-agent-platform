"""本机部署验收：真实 HTTP/PG/Redis，模拟模型；临时订单按 nonce 清理。"""

import json
from pathlib import Path
import secrets
import sys
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auth import _configured_tokens  # noqa: E402
from database import create_database_connection  # noqa: E402


def run() -> dict:
    users = list(_configured_tokens().values())
    owner = next(u for u in users if u.user_id == "U-1001")
    other = next(u for u in users if u.user_id == "U-1002")
    marker = "本机验收-" + uuid.uuid4().hex
    fixtures, checks = [], []

    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    def count_applications(order_id):
        with create_database_connection() as db:
            return db.execute("SELECT count(*) FROM return_applications WHERE order_id=%s",
                              (order_id,)).fetchone()[0]

    def fixture(category="STANDARD"):
        with create_database_connection() as db:
            for _ in range(100):
                order_id = f"O-{8000 + secrets.randbelow(1000)}"
                inserted = db.execute(
                    """INSERT INTO orders(id, owner_user_id, status, product, delivered_at, amount_cents)
                       VALUES (%s, %s, 'delivered', %s, CURRENT_DATE-1, 3555)
                       ON CONFLICT(id) DO NOTHING RETURNING id""",
                    (order_id, owner.user_id, marker),
                ).fetchone()
                if inserted:
                    break
            else:
                raise RuntimeError("无法分配无冲突的虚构订单")
            if category is not None:
                db.execute(
                    """INSERT INTO order_return_policies(order_id, category, product_condition,
                        exclusion_confirmed_at_purchase, downloaded_or_unsealed, activated,
                        merchant_no_reason_days, policy_version)
                       VALUES (%s,%s,'INTACT',true,false,false,0,'local-smoke-v1')""",
                    (order_id, category),
                )
        fixtures.append(order_id)
        return order_id

    def expect(client, method, path, status=200, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code != status:
            raise AssertionError(f"{method} {path}: expected {status}, got {response.status_code}: {response.text[:300]}")
        return response.json() if "json" in response.headers.get("content-type", "") else response.text

    try:
        with httpx.Client(base_url="http://127.0.0.1:8011", timeout=30, trust_env=False) as anonymous, \
             httpx.Client(base_url="http://127.0.0.1:8011", timeout=30, trust_env=False,
                          headers={"Authorization": f"Bearer {owner.token}"}) as customer, \
             httpx.Client(base_url="http://127.0.0.1:8011", timeout=30, trust_env=False,
                          headers={"Authorization": f"Bearer {other.token}"}) as outsider:
            expect(anonymous, "GET", "/health")
            check("demo_page", "O-7101" in expect(anonymous, "GET", "/demo"))
            check("service_page", "O-7101" in expect(anonymous, "GET", "/service"))
            standard = fixture()
            expect(anonymous, "GET", f"/service/orders/{standard}", status=401)
            expect(outsider, "GET", f"/service/orders/{standard}", status=404)
            check("identity_isolation", True)
            with httpx.Client(timeout=5, trust_env=False) as direct:
                result = direct.get(f"http://127.0.0.1:8081/api/orders/{standard}",
                                    headers={"Authorization": f"Bearer {owner.token}"})
                check("java_requires_internal_credential", result.status_code in {401, 403})
            tid = "local-ab-" + uuid.uuid4().hex
            started = expect(customer, "POST", "/agent-graph/runs", json={
                "thread_id": tid, "message": f"我要退货 {standard}", "mode": "mock"})
            check("agent_waits_for_confirmation", started["status"] == "WAITING_CONFIRMATION")
            check("zero_application_before_confirmation", count_applications(standard) == 0)
            expect(outsider, "GET", f"/agent-graph/runs/{tid}", status=404)
            restored = expect(customer, "GET", f"/agent-graph/runs/{tid}")
            check("checkpoint_read", restored["return_draft"]["draft_id"] == started["return_draft"]["draft_id"])
            accepted = expect(customer, "POST", f"/agent-graph/runs/{tid}/resume", json={"approved": True})
            duplicate = expect(customer, "POST", f"/agent-graph/runs/{tid}/resume", json={"approved": True})
            check("agent_submission", accepted["status"] == "COMPLETED" and
                  accepted["return_application"]["status"] == "SUBMITTED")
            check("duplicate_confirmation", duplicate["return_application"] == accepted["return_application"]
                  and count_applications(standard) == 1)
            sources = expect(customer, "POST", "/knowledge/search",
                             json={"query": "退款多久到账", "limit": 3})
            check("persistent_rag_citations", bool(sources) and all(
                r.get("chunk_id") and r.get("page", 0) > 0 and r.get("content") for r in sources))
            joined = expect(customer, "POST", "/agent-graph/runs", json={
                "thread_id": "local-ab-" + uuid.uuid4().hex,
                "message": f"结合订单 {standard}，退款多久到账？", "mode": "mock"})
            tools = {t["tool_name"] for t in joined["tool_trace"]}
            check("joint_order_policy_flow", joined["status"] == "COMPLETED" and
                  {"query_order", "search_knowledge_base"} <= tools)
            changed = fixture()
            draft = expect(customer, "POST", f"/service/orders/{changed}/return-drafts")
            path = f"/service/orders/{changed}/return-drafts/{draft['draft_id']}"
            snapshot = {k: draft[k] for k in ("product", "amount_cents", "expires_at")}
            expect(customer, "POST", path + "/confirm", json={**snapshot, "amount_cents": 1}, status=409)
            with create_database_connection() as db:
                db.execute("UPDATE orders SET amount_cents=4999 WHERE id=%s AND product=%s", (changed, marker))
            expect(customer, "POST", path + "/confirm", json=snapshot, status=409)
            check("stale_confirmation_no_write", count_applications(changed) == 0)
            refreshed = expect(customer, "POST", path + "/refresh")
            check("refresh_changes_draft", refreshed["draft_id"] != draft["draft_id"])
            fresh_path = f"/service/orders/{changed}/return-drafts/{refreshed['draft_id']}"
            result = expect(customer, "POST", fresh_path + "/confirm",
                            json={k: refreshed[k] for k in snapshot})
            check("fresh_confirmation_matches_new_amount", result["refund_amount_cents"] == 4999)
            for category in ("STANDARD", "FRESH_PERISHABLE", None):
                order_id = fixture(category)
                decision = expect(customer, "GET", f"/service/orders/{order_id}/return-eligibility")
                if category != "STANDARD":
                    check(f"{category}_not_auto_approved", decision["decision"] == "REASON_REQUIRED")
                d = expect(customer, "POST", f"/service/orders/{order_id}/return-drafts")
                p = f"/service/orders/{order_id}/return-drafts/{d['draft_id']}"
                review = expect(customer, "POST", p + "/reason",
                                json={"return_reason": "商品质量需要核实", "reason_code": "QUALITY_ISSUE"})
                check(f"{category}_quality_manual_review", review["decision"] == "MANUAL_REVIEW")
                expect(customer, "POST", p + "/confirm",
                       json={k: d[k] for k in ("product", "amount_cents", "expires_at")}, status=409)
                check(f"{category}_manual_review_blocks_write", count_applications(order_id) == 0)
        return {"checks_passed": len(checks), "checks": checks, "model_mode": "mock",
                "external_model_calls": 0, "temporary_orders": len(fixtures)}
    finally:
        with create_database_connection() as db:
            for order_id in fixtures:
                owned = db.execute("SELECT id FROM orders WHERE id=%s AND product=%s FOR UPDATE",
                                   (order_id, marker)).fetchone()
                if not owned:
                    raise RuntimeError("验收订单发生外部变化，停止清理")
                db.execute("DELETE FROM return_applications WHERE order_id=%s", (order_id,))
                db.execute("DELETE FROM return_drafts WHERE order_id=%s", (order_id,))
                db.execute("DELETE FROM order_return_policies WHERE order_id=%s", (order_id,))
                db.execute("DELETE FROM orders WHERE id=%s AND product=%s", (order_id, marker))


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
