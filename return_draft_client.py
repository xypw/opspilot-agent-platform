"""Java 限时草稿网关：时间由 Java 产生和判断。"""
from datetime import timedelta
from uuid import UUID
import re
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator
from typing import Literal
from order_query_factory import ConfiguredJavaOrderQuery
from order_service_client import OrderServiceError
from return_review_client import ReturnReviewRequest, ReturnReviewResponse


class ReturnDraftExpired(OrderServiceError):
    pass


class ReturnOrderChanged(Exception):
    """确定的业务冲突，与可重试的上游服务故障分开处理。"""

    def __init__(self):
        super().__init__("订单商品、金额或签收状态已发生变化，本次未创建退货申请。请核对最新订单信息，重新确认后再申请。")


class ReturnDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID
    order_id: str = Field(pattern=r"^O-[0-9]{4}$")
    product: str = Field(min_length=1)
    amount_cents: StrictInt = Field(ge=0)
    started_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["WAITING_REASON", "EXPIRED", "REVIEWED", "SUBMITTED", "CANCELLED"]

    @model_validator(mode="after")
    def check_duration(self):
        if self.expires_at - self.started_at != timedelta(hours=1):
            raise ValueError("草稿有效期不符合契约")
        return self


class ReturnApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    order_id: str = Field(pattern=r"^O-[0-9]{4}$")
    product: str = Field(min_length=1)
    refund_amount_cents: StrictInt = Field(ge=0)
    status: Literal["SUBMITTED"]
    created_at: AwareDatetime


class JavaReturnDraftGateway:
    def __init__(self, service_url):
        self.service_url = ConfiguredJavaOrderQuery(service_url).service_url

    def _request(self, method, order_id, suffix="", payload=None, response_model=ReturnDraftResponse):
        if re.fullmatch(r"O-[0-9]{4}", order_id) is None:
            raise ValueError("订单编号格式不正确")
        try:
            with httpx.Client(base_url=self.service_url, timeout=2.0,
                              trust_env=False, follow_redirects=False) as http:
                response = http.request(method, f"/api/orders/{order_id}/return-draft{suffix}", json=payload)
        except httpx.RequestError:
            raise OrderServiceError("草稿服务暂时不可用，请重试") from None
        if response.status_code == 409:
            try:
                order_changed = response.json() == {"code": "ORDER_CHANGED"}
            except ValueError:
                order_changed = False
            if order_changed:
                raise ReturnOrderChanged()
        if response.status_code == 410:
            try:
                expired = response.json() == {"code": "DRAFT_EXPIRED"}
            except ValueError:
                expired = False
            if expired:
                raise ReturnDraftExpired("退货草稿已超过1小时有效期")
        if response.status_code != 200:
            raise OrderServiceError("无法完成草稿操作，请检查服务或订单状态")
        try:
            result = response_model.model_validate_json(response.content)
            if result.order_id != order_id:
                raise ValueError("订单不一致")
            if isinstance(result, ReturnReviewResponse):
                expected = {"NO_REASON_WINDOW": "ACCEPTABLE",
                            "EVIDENCE_REVIEW_REQUIRED": "MANUAL_REVIEW"}.get(result.reason, "REJECTED")
                if result.decision != expected:
                    raise ValueError("预审响应矛盾")
        except ValueError:
            raise OrderServiceError("草稿服务响应不符合契约") from None
        return result.model_dump(mode="json")

    def start(self, order_id):
        return self._request("POST", order_id)

    def get(self, order_id, draft_id):
        result = self._request("GET", order_id, f"/{UUID(draft_id)}")
        if result["draft_id"] != str(UUID(draft_id)):
            raise OrderServiceError("返回的草稿编号不一致")
        return result

    def submit(self, order_id, draft_id, reason, code):
        request = ReturnReviewRequest(return_reason=reason, reason_code=code)
        return self._request("POST", order_id, f"/{UUID(draft_id)}/reason",
                             request.model_dump(), ReturnReviewResponse)

    def confirm(self, order_id, draft_id):
        return self._request(
            "POST", order_id, f"/{UUID(draft_id)}/confirm",
            response_model=ReturnApplicationResponse,
        )

    def cancel(self, order_id, draft_id):
        return self._request("POST", order_id, f"/{UUID(draft_id)}/cancel")

    def refresh(self, order_id, draft_id):
        return self._request("POST", order_id, f"/{UUID(draft_id)}/refresh")
