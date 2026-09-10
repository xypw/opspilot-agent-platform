"""可恢复 Agent 运行的公开状态模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunStartRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class AgentRunCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    # 状态是 Agent 工作流的事实来源；API 客户端据此决定显示确认、完成或取消结果。
    status: Literal["WAITING_CONFIRMATION", "COMPLETED", "CANCELLED"]
    user_message: str = Field(min_length=1)
    pending_action_id: str | None
    tool_name: str
    tool_result: dict[str, str] | None
    answer: str = Field(min_length=1)
    version: int = Field(ge=1)
    model_requests: int = Field(ge=0)
    simulated_model_requests: int = Field(ge=0)
