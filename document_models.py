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
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)
    heading_path: list[str] = Field(default_factory=list)
    validation_status: Literal["validated", "needs_review"] = "validated"
    validation_reasons: list[str] = Field(default_factory=list)
    parser_version: str | None = None


class DocumentPageResponse(BaseModel):
    page: int = Field(ge=1)
    validation_status: Literal["validated", "needs_review"]
    validation_reasons: list[str] = Field(default_factory=list)
    parser_version: str
    critical_terms: list[dict] = Field(default_factory=list)


class DocumentIngestionResponse(BaseModel):
    document_id: str
    title: str
    status: Literal["ready", "partial"]
    publication_status: Literal["published", "pending_review"] = "published"
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=0)
    ocr_required_pages: list[int]
    chunks: list[DocumentChunkResponse]
    pages: list[DocumentPageResponse] = Field(default_factory=list)


class DocumentPublishRequest(BaseModel):
    pages: list[int] = Field(min_length=1)


class DocumentPublishResponse(BaseModel):
    document_id: str
    published_pages: list[int]
    remaining_pages: list[int]
    published_chunk_count: int
