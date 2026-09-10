"""人工确认操作的状态模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PendingAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    tool_name: Literal["change_ticket_priority"]
    ticket_id: str = Field(min_length=1)
    previous_priority: Literal["low", "medium", "high"]
    new_priority: Literal["low", "medium", "high"]
    # cancelled 让底层写操作也知道“用户已经反悔”，不能只靠上层页面隐藏按钮。
    status: Literal["pending", "executed", "cancelled"]
