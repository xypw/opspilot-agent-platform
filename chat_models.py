"""POST /chat 的请求和响应契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from knowledge_models import KnowledgeSearchResult
from order_service_client import OrderResponse, ReturnEligibilityResponse


class ChatRequest(BaseModel):
    # 额外字段拒绝，避免把拼错的配置静默忽略。
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=1, max_length=1000, examples=["帮我查工单 T-1003", "帮我查订单 O-2003"])
    mode: Literal["mock", "live"] = Field(
        default="mock", description="mock 为规则模拟，不读密钥；live 才调用真实模型，不自动回退。"
    )


class ChatResponse(BaseModel):
    mode: Literal["mock", "live"]
    is_mock: bool
    model: str
    answer: str
    tool_name: str
    # 订单包含日期、整数和 null，不能错误地声明成全部字符串。
    tool_result: ReturnEligibilityResponse | OrderResponse | dict[str, str] | list[KnowledgeSearchResult] | None
    model_requests: int = Field(ge=0, description="真实外部模型请求次数；mock 固定为 0。")
    simulated_model_requests: int = Field(ge=0, description="仅模拟模式使用的 HTTP 交互次数。")
