"""Java 订单服务的只读 HTTP 客户端；不加载 .env，不调用大模型。

业务上的“没有订单”与技术上的“查询失败”必须区分，否则 Agent 会把故障
错误地解释为订单不存在。客户端负责这层边界，不负责退款等业务决策。
"""

import re
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class OrderServiceError(RuntimeError):
    """可安全展示的业务服务错误，不包含上游原始响应、URL 或请求头。"""


class OrderResponse(BaseModel):
    """Java 返回值也要校验，不能只检查发给 Java 的请求。"""

    # 日期允许从 JSON 的 ISO 字符串解析；金额单独使用 StrictInt 禁止浮点数。
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^O-[0-9]{4}$")
    status: Literal["delivered", "shipped", "processing", "cancelled"]
    product: str = Field(min_length=1)
    delivered_at: date | None
    amount_cents: StrictInt = Field(ge=0)


class ReturnEligibilityResponse(BaseModel):
    """校验 Java 的退货决策，并检查字段之间不能互相矛盾。"""

    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^O-[0-9]{4}$")
    decision: Literal["NO_REASON_ALLOWED", "REASON_REQUIRED", "NOT_ALLOWED"]
    can_apply: StrictBool
    reason_required: StrictBool
    days_since_delivery: StrictInt | None = Field(default=None, ge=0)
    reason: Literal[
        "WITHIN_7_DAY_NO_REASON_WINDOW",
        "WITHIN_15_DAY_CONDITIONAL_WINDOW",
        "RETURN_WINDOW_EXPIRED",
        "ORDER_NOT_DELIVERED",
        "ORDER_CANCELLED",
    ]

    @model_validator(mode="after")
    def decision_must_match_details(self):
        if self.decision == "NO_REASON_ALLOWED":
            valid = (
                self.can_apply
                and not self.reason_required
                and self.days_since_delivery is not None
                and self.days_since_delivery <= 7
                and self.reason == "WITHIN_7_DAY_NO_REASON_WINDOW"
            )
        elif self.decision == "REASON_REQUIRED":
            valid = (
                self.can_apply
                and self.reason_required
                and self.days_since_delivery is not None
                and 8 <= self.days_since_delivery <= 15
                and self.reason == "WITHIN_15_DAY_CONDITIONAL_WINDOW"
            )
        else:
            valid = (
                not self.can_apply
                and not self.reason_required
                and (
                    (self.reason == "RETURN_WINDOW_EXPIRED"
                     and self.days_since_delivery is not None
                     and self.days_since_delivery >= 16)
                    or (self.reason in {"ORDER_NOT_DELIVERED", "ORDER_CANCELLED"}
                        and self.days_since_delivery is None)
                )
            )
        if not valid:
            raise ValueError("退货资格字段之间存在矛盾")
        return self


class JavaOrderClient:
    """接收由程序配置的 HTTP 客户端；模型只提供订单号，不能指定目标 URL。"""

    def __init__(self, http: httpx.Client):
        self.http = http

    def get_by_id(self, order_id: str) -> dict[str, object] | None:
        # 将订单号限制为单个路径参数，拒绝 ../ 等路径输入。
        if not isinstance(order_id, str) or re.fullmatch(r"O-[0-9]{4}", order_id) is None:
            raise ValueError("订单编号必须是 O- 加四位数字")

        try:
            response = self.http.get(f"/api/orders/{order_id}")
        except httpx.TimeoutException:
            raise OrderServiceError("订单服务查询超时，请稍后重试") from None
        except httpx.RequestError:
            raise OrderServiceError("无法连接订单服务，请检查服务状态") from None

        # 只把契约中明确的业务 404 映射为 None，错误地址的 404 仍是故障。
        if response.status_code == 404:
            try:
                if response.json() == {"code": "ORDER_NOT_FOUND"}:
                    return None
            except ValueError:
                pass
            raise OrderServiceError("订单服务返回了非预期的 404 响应")

        # 不把 500/403/重定向等失败伪装成“查不到订单”，也不悄悄读本地假数据。
        if response.status_code != 200:
            raise OrderServiceError("订单服务查询失败，请检查服务状态")

        try:
            # 直接按 JSON 契约校验，ISO 日期会转换成 date，整数金额仍保持严格类型。
            order = OrderResponse.model_validate_json(response.content)
        except ValueError:
            raise OrderServiceError("订单服务返回的数据不符合接口契约") from None
        if order.id != order_id:
            raise OrderServiceError("订单服务返回的编号与请求不一致")
        # JSON 模式把 date 恢复成 ISO 字符串，方便后续工具消息序列化。
        return order.model_dump(mode="json")

    def get_return_eligibility(self, order_id: str) -> dict[str, object] | None:
        """查询 Java 计算的退货资格；Python 不重复实现日期规则。"""
        if not isinstance(order_id, str) or re.fullmatch(r"O-[0-9]{4}", order_id) is None:
            raise ValueError("订单编号必须是 O- 加四位数字")

        try:
            response = self.http.get(f"/api/orders/{order_id}/return-eligibility")
        except httpx.TimeoutException:
            raise OrderServiceError("退货资格查询超时，请稍后重试") from None
        except httpx.RequestError:
            raise OrderServiceError("无法连接订单服务，请检查服务状态") from None

        if response.status_code == 404:
            try:
                if response.json() == {"code": "ORDER_NOT_FOUND"}:
                    return None
            except ValueError:
                pass
            raise OrderServiceError("订单服务返回了非预期的 404 响应")
        if response.status_code != 200:
            raise OrderServiceError("退货资格查询失败，请检查订单服务状态")

        try:
            result = ReturnEligibilityResponse.model_validate_json(response.content)
        except ValueError:
            raise OrderServiceError("订单服务返回的退货资格不符合接口契约") from None
        if result.order_id != order_id:
            raise OrderServiceError("退货资格返回的订单编号与请求不一致")
        return result.model_dump(mode="json")
