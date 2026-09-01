"""在查询执行前，分别校验并规范化工单编号和订单编号。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryTicketArgs(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticket_id: str = Field(min_length=1)


class QueryOrderArgs(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    order_id: str = Field(min_length=1)


class SearchKnowledgeBaseArgs(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=3, ge=1, le=5)


class RequestPriorityChangeArgs(BaseModel):
    """模型只能申请修改，不能绕过确认直接执行修改。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    new_priority: Literal["low", "medium", "high"]
