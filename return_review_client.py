"""申请前审核的跨服务契约；原因分类不代表证据已核实。"""

import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from order_service_client import OrderServiceError

ReasonCode = Literal["PERSONAL_PREFERENCE", "QUALITY_ISSUE", "OTHER"]


class ReturnReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    return_reason: str = Field(min_length=1, max_length=1000)
    reason_code: ReasonCode = "OTHER"


class ReturnReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^O-[0-9]{4}$")
    decision: Literal["ACCEPTABLE", "REJECTED", "MANUAL_REVIEW"]
    reason: Literal["NO_REASON_WINDOW", "PERSONAL_REASON_OUTSIDE_WINDOW",
                    "EVIDENCE_REVIEW_REQUIRED", "ORDER_CANCELLED",
                    "ORDER_NOT_DELIVERED", "RETURN_WINDOW_EXPIRED"]


def review_return(http: httpx.Client, order_id: str, reason: str, code: ReasonCode) -> dict:
    if not isinstance(order_id, str) or re.fullmatch(r"O-[0-9]{4}", order_id) is None:
        raise ValueError("订单编号格式不正确")
    request = ReturnReviewRequest(return_reason=reason, reason_code=code)
    try:
        response = http.post(f"/api/orders/{order_id}/return-review",
                             json=request.model_dump(), follow_redirects=False)
    except httpx.RequestError:
        raise OrderServiceError("无法完成退货预审，请稍后重试") from None
    if response.status_code != 200:
        # 审核过程中订单丢失/服务失败不等于业务拒绝。
        raise OrderServiceError("退货预审失败，请检查订单服务状态")
    try:
        result = ReturnReviewResponse.model_validate_json(response.content)
        expected = {
            "NO_REASON_WINDOW": "ACCEPTABLE",
            "EVIDENCE_REVIEW_REQUIRED": "MANUAL_REVIEW",
        }.get(result.reason, "REJECTED")
        if result.order_id != order_id or result.decision != expected:
            raise ValueError("预审响应不一致")
    except ValueError:
        raise OrderServiceError("退货预审响应不符合接口契约") from None
    return result.model_dump()
