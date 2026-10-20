from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    chat_model: str = "gemini-2.5-flash"
    chat_fallback_model: str = "gemini-flash-latest"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    vision_model: str = ""
    max_image_edge_px: int = 1568
    image_jpeg_threshold_bytes: int = 512 * 1024
    image_jpeg_quality: int = 88
    min_transcription_chars: int = 40
    data_dir: Path = BACKEND_DIR / "data"
    upload_dir: Path | None = None
    chroma_dir: Path | None = None
    max_upload_mb: int = 25
    max_upload_pages: int = 1_000
    chunk_size: int = 600
    chunk_overlap: int = 150
    embedding_batch_size: int = 100
    max_documents: int = 50

    top_k: int = 6
    candidate_k: int = 24
    enable_mmr: bool = False
    min_relevance: float = 0.30
    min_lexical_score: float = 6.0
    history_turns: int = 6

    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    rate_limit_per_minute: int = 30
    api_key: str = ""
    trust_forwarded_for: bool = False
    enable_metrics: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so CORS_ORIGINS works in .env."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _derive_storage_paths(self) -> Settings:
        if self.upload_dir is None:
            self.upload_dir = self.data_dir / "uploads"
        if self.chroma_dir is None:
            self.chroma_dir = self.data_dir / "chroma"
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def resolved_vision_model(self) -> str:
        """`VISION_MODEL` when set, otherwise the chat model — which is already
        multimodal for every current Flash model."""
        return self.vision_model or self.chat_model

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.chroma_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
