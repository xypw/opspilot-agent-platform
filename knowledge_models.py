"""知识库检索接口的请求与响应契约。"""

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=3, ge=1, le=5)


class KnowledgeSearchResult(BaseModel):
    chunk_id: str
    title: str
    page: int = Field(ge=1)
    content: str
    score: float
