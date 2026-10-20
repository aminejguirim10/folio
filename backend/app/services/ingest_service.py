from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core.security import resolve_within
from app.services.chunk_service import chunk_pages
from app.services.gemini_provider import get_provider
from app.services.loaders import LoadError, load_document
from app.services.vector_service import get_index

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    doc_id: str
    source: str
    doc_type: str
    pages: int
    chunks: int
    replaced: bool
    truncated: bool = False
    total_pages: int | None = None
    confidence: str = ""
    notes: str = ""


class IngestError(RuntimeError):
    """Raised for user-correctable ingestion problems (surfaced as 400)."""


def _doc_id_for(filename: str, data: bytes) -> str:
    """Content-addressed so re-uploading an identical file is idempotent."""
    return hashlib.sha256(data).hexdigest()[:16]


def ingest_document(source_path: Path, *, filename: str, stored_as: str) -> IngestResult:
    """Index any supported document that has already been written to disk.

    `filename` is the display name; `stored_as` is the sanitised on-disk name.
    Format dispatch, validation and the per-format unit cap all live in
    `loaders`; this function owns the index lifecycle and the byte budget.
    """
    settings = get_settings()
    if not source_path.exists():
        raise IngestError("Uploaded file is missing.")

    try:
        loaded = load_document(
            source_path, filename, max_units=settings.max_upload_pages
        )
    except LoadError as exc:
        raise IngestError(str(exc)) from exc

    if loaded.capped or loaded.total_units > settings.max_upload_pages:
        noun = "pages" if loaded.doc_type == "pdf" else "sections"
        raise IngestError(
            f"Document is too large: {loaded.total_units} {noun}, over the "
            f"{settings.max_upload_pages}-{noun}-per-file limit. Split it into "
            "smaller files and upload them separately."
        )

    doc_id = _doc_id_for(filename, source_path.read_bytes())
    index = get_index()
    existed = index.has_document(doc_id)

    if index.document_count() >= settings.max_documents and not existed:
        raise IngestError(
            f"Library is full ({settings.max_documents} documents). Delete one first."
        )

    chunks = chunk_pages(loaded.units, doc_id=doc_id, source=filename)
    if not chunks:
        raise IngestError(
            "No indexable content found — the file may be empty or image-only."
        )

    embeddings = get_provider().embed_documents([c.text for c in chunks])
    index.replace_document(
        doc_id=doc_id,
        source=filename,
        chunks=chunks,
        embeddings=embeddings,
    )

    target = resolve_within(settings.upload_dir, stored_as)
    if not target.exists():
        shutil.copy2(source_path, target)

    log.info(
        "Indexed %s (%s): %d units, %d chunks%s",
        filename, loaded.doc_type, len(loaded.units), len(chunks),
        f", confidence={loaded.confidence}" if loaded.confidence else "",
    )
    return IngestResult(
        doc_id=doc_id,
        source=filename,
        doc_type=loaded.doc_type,
        pages=len(loaded.units),
        chunks=len(chunks),
        replaced=existed,
        truncated=False,
        total_pages=loaded.total_units,
        confidence=loaded.confidence,
        notes=loaded.notes,
    )


ingest_pdf = ingest_document


def new_upload_path(stored_as: str) -> Path:
    return resolve_within(get_settings().upload_dir, stored_as)


def temp_upload_path(suffix: str = "") -> Path:
    """Scratch path for streaming the upload before it is validated."""
    settings = get_settings()
    return settings.upload_dir / f".tmp-{uuid.uuid4().hex}{suffix}"
