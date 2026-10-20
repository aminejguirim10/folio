from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class CitationOut(BaseModel):
    doc_id: str
    source: str
    page: int
    score: float
    snippet: str
    locator: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    previous_interaction_id: str | None = None
    doc_ids: list[str] | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    grounded: bool
    interaction_id: str | None = None
    model: str


class DocumentOut(BaseModel):
    doc_id: str
    source: str
    doc_type: str = "pdf"
    confidence: str = ""
    notes: str = ""
    pages: int
    chunks: int
    indexed_at: str


class UploadResponse(BaseModel):
    message: str
    doc_id: str
    source: str
    doc_type: str = "pdf"
    confidence: str = ""
    notes: str = ""
    pages: int
    chunks: int
    replaced: bool
    truncated: bool = False
    total_pages: int | None = None


class PreviewChunkOut(BaseModel):
    """One indexed chunk, as shown in the client's content preview."""
    index: int
    page: int
    locator: str = ""
    text: str


class DocumentPreviewOut(BaseModel):
    doc_id: str
    source: str
    doc_type: str = "pdf"
    pages: int
    chunks: int
    offset: int
    items: list[PreviewChunkOut]
    has_more: bool


class FormatInfo(BaseModel):
    """One entry of the supported-format list surfaced to the client."""

    doc_type: str
    extensions: list[str]
    description: str
    available: bool = True


class FormatsResponse(BaseModel):
    formats: list[FormatInfo]
    unsupported: dict[str, str]


class LimitsResponse(BaseModel):
    max_upload_mb: int
    max_documents: int
    max_units_per_document: int
    extensions: list[str]


class HealthResponse(BaseModel):
    status: str
    provider_configured: bool
    documents: int
    chunks: int
    model: str
    details: dict[str, Any] = Field(default_factory=dict)
