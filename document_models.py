"""文档上传和入库结果的 API 契约。"""

from typing import Literal

from pydantic import BaseModel, Field


class DocumentChunkResponse(BaseModel):
    chunk_id: str
    title: str
    page: int = Field(ge=1)
    content: str
    extraction_method: Literal["text", "ocr"]
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)


class DocumentIngestionResponse(BaseModel):
    document_id: str
    title: str
    status: Literal["ready", "partial"]
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=0)
    ocr_required_pages: list[int]
    chunks: list[DocumentChunkResponse]
