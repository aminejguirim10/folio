from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.config import get_settings
from app.core.security import check_signature, display_filename, sanitize_filename
from app.models.schemas import (
    DocumentOut,
    DocumentPreviewOut,
    FormatInfo,
    FormatsResponse,
    LimitsResponse,
    PreviewChunkOut,
    UploadResponse,
)
from app.services.catalog_service import DocumentCatalog, get_catalog
from app.services.file_service import (
    StoredFileMissing,
    media_type_for,
    preview_kind_for,
    resolve_stored_file,
)
from app.services.ingest_service import IngestError, ingest_document, temp_upload_path
from app.services.loaders import (
    LoadError,
    all_formats,
    doc_type_for,
    known_unsupported,
    missing_dependency_formats,
    spec_for_extension,
    supported_extensions,
    supported_formats,
    unsupported_reason,
)
from app.services.office_service import (
    ConversionUnavailable,
    convert_locked,
    is_office,
    libreoffice_available,
)
from app.services.vector_service import get_index

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

_HEAD_BYTES = 16


def _catalog() -> DocumentCatalog:
    return get_catalog()


@router.get("/formats", response_model=FormatsResponse)
async def list_formats() -> FormatsResponse:
    """What this deployment can actually read, given the installed packages.

    A format whose optional parser is missing is still listed, with
    `available: false`, so the UI can grey it out and say why instead of
    letting the user discover it through a failed upload.
    """
    available = {s.doc_type for s in supported_formats()}
    formats = [
        FormatInfo(
            doc_type=spec.doc_type,
            extensions=sorted(spec.extensions),
            description=spec.description,
            available=spec.doc_type in available,
        )
        for spec in all_formats()
    ]
    reasons = known_unsupported()
    for spec in missing_dependency_formats():
        for ext in spec.extensions:
            reasons[ext] = f"{ext} needs the '{spec.requires}' package, which is not installed."
    return FormatsResponse(formats=formats, unsupported=reasons)


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    settings = get_settings()
    original_name = file.filename or ""
    safe_name = sanitize_filename(original_name, fallback="document")
    display_name = display_filename(original_name)

    if spec_for_extension(Path(safe_name).suffix) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=unsupported_reason(original_name or safe_name),
        )

    extension = Path(safe_name).suffix
    total = 0
    head = b""
    tmp = temp_upload_path(extension)
    try:
        with tmp.open("wb") as buffer:
            while chunk := await file.read(1024 * 256):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
                    )
                if len(head) < _HEAD_BYTES:
                    head = (head + chunk)[:_HEAD_BYTES]
                buffer.write(chunk)

        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload."
            )
        check_signature(head, original_name or safe_name)

        try:
            result = ingest_document(
                tmp, filename=display_name, stored_as=safe_name
            )
        except IngestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except LoadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except Exception as exc: 
            log.exception("Ingestion failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not index this document: {exc}",
            ) from exc
    finally:
        tmp.unlink(missing_ok=True)

    _catalog().record(
        doc_id=result.doc_id,
        source=result.source,
        pages=result.pages,
        chunks=result.chunks,
        doc_type=result.doc_type,
        confidence=result.confidence,
        notes=result.notes,
        stored_as=safe_name,
    )


    message = "Re-indexed." if result.replaced else "Indexed."
    if result.confidence == "low":
        message = "Indexed, but the image was hard to read — answers may be unreliable."
    elif result.confidence == "medium":
        message = "Indexed with some uncertain readings."

    return UploadResponse(
        message=message,
        doc_id=result.doc_id,
        source=result.source,
        doc_type=result.doc_type,
        confidence=result.confidence,
        notes=result.notes,
        pages=result.pages,
        chunks=result.chunks,
        replaced=result.replaced,
        truncated=result.truncated,
        total_pages=result.total_pages,
    )


@router.get("", response_model=list[DocumentOut])
async def list_documents() -> list[DocumentOut]:
    return [
        DocumentOut(
            doc_id=str(record["doc_id"]),
            source=str(record["source"]),
            doc_type=str(record.get("doc_type") or doc_type_for(str(record["source"]))),
            confidence=str(record.get("confidence") or ""),
            notes=str(record.get("notes") or ""),
            pages=int(record["pages"]),
            chunks=int(record["chunks"]),
            indexed_at=str(record["indexed_at"]),
        )
        for record in _catalog().list()
    ]


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: str) -> None:
    get_index().delete_document(doc_id)
    _catalog().remove(doc_id)


@router.get("/limits", response_model=LimitsResponse)
async def upload_limits() -> LimitsResponse:
    """Upload ceilings, so the UI can reject a file before sending it."""
    settings = get_settings()
    return LimitsResponse(
        max_upload_mb=settings.max_upload_mb,
        max_documents=settings.max_documents,
        max_units_per_document=settings.max_upload_pages,
        extensions=supported_extensions(),
    )


def _frame_ancestors() -> str:
    """CSP `frame-ancestors` listing the origins allowed to embed the file.

    Derived from CORS_ORIGINS so there is one place to configure "where the
    front end lives" — adding a host to the CORS list also lets it preview.
    """
    allowed = ["'self'"]
    for origin in get_settings().cors_origins:
        candidate = origin.strip().rstrip("/")
        if candidate and " " not in candidate:
            allowed.append(candidate)
    return "frame-ancestors " + " ".join(allowed)


_PREVIEW_PAGE_SIZE = 60


def _content_disposition(kind: str, download_name: str) -> str:
    """How the browser should treat the response.

    Everything the client renders inside its own sandboxed frame or an
    `<img>`/`<iframe>` is served `inline`. HTML is *not*: an uploaded `.html`
    file rendered in the top-level browsing context would run script in this
    app's origin, with access to its session. It is offered as a download
    instead, and the client renders it from fetched text inside a sandboxed
    frame where scripts cannot run.
    """
    ascii_name = download_name.encode("ascii", "replace").decode().replace('"', "'")
    quoted = urllib.parse.quote(download_name)
    disposition = "attachment" if kind == "text" and download_name.lower().endswith(
        (".html", ".htm")
    ) else "inline"
    return (
        f"{disposition}; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quoted}"
    )


def _record_and_file(doc_id: str) -> tuple[dict, Path]:
    """The catalogue record and verified on-disk original for a document.

    Shared by `/file` and `/preview.pdf` so both agree, byte for byte, on
    which file a `doc_id` refers to.
    """
    record = next((r for r in _catalog().list() if str(r["doc_id"]) == doc_id), None)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    try:
        path = resolve_stored_file(
            doc_id,
            stored_as=str(record.get("stored_as") or ""),
            source=str(record["source"]),
        )
    except StoredFileMissing as exc:
        log.info("Original file unavailable for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The original file is no longer stored on the server.",
        ) from exc
    return record, path


@router.get("/{doc_id}/file", response_class=FileResponse)
async def document_file(doc_id: str) -> FileResponse:
    """The original uploaded file, served for the content preview.

    Ingestion keeps the uploaded bytes in `upload_dir`; only the temporary
    streaming copy is deleted. `resolve_stored_file` re-derives the on-disk
    name and then *verifies* it by hashing the file, because a name match
    alone cannot prove the file is this document.

    404 (not an empty 200) when the original is gone — an operator who clears
    `upload_dir` to reclaim space should get an honest "not available" and see
    the extracted-text preview instead, not an empty viewer.
    """
    record, path = _record_and_file(doc_id)

    media_type = media_type_for(path)
    kind = preview_kind_for(path)
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(kind, display_filename(
                str(record["source"]), fallback=path.name
            )),
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _frame_ancestors(),
        },
    )


@router.head("/{doc_id}/file")
async def document_file_head(doc_id: str) -> FileResponse:
    """Existence check for the original file, without transferring it.

    Declared separately rather than as a second method on the GET route:
    that keeps each operation's OpenAPI id unique, and the client uses this
    to find out whether the original still exists *before* pointing an
    `<iframe>` at a URL that may 404 — an `<iframe>` renders a 404 as a
    blank frame with no error event, which looks like a broken feature
    rather than "the original was cleaned up".
    """
    return await document_file(doc_id)


@router.get("/{doc_id}/preview.pdf", response_class=FileResponse)
async def document_preview_pdf(doc_id: str) -> FileResponse:
    """A viewable PDF for the preview, converting Office files on demand.

    Keeps `/file` honest — it always serves the exact original bytes — while
    giving the UI something a browser can actually display for `.docx`,
    `.xlsx` and `.pptx`. A PDF passes straight through; anything else that is
    not an Office format is a client mistake, not a server one.

    503 when LibreOffice is absent, which is a configuration fact the UI
    surfaces as "showing the extracted text instead" rather than an error
    page. The first conversion costs seconds; later ones are served from the
    on-disk cache.
    """
    return await _render_preview_pdf(doc_id)


@router.head("/{doc_id}/preview.pdf")
async def document_preview_pdf_head(doc_id: str) -> FileResponse:
    """Whether this document can be previewed as a PDF, without rendering it.

    The client probes with HEAD before choosing between the document view and
    the extracted-text fallback. A GET here would convert the whole file just
    to answer a yes/no question, and the first Office conversion is slow.
    """
    record, path = _record_and_file(doc_id)

    if path.suffix.lower() == ".pdf":
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": _frame_ancestors(),
            },
        )
    if is_office(path):
        if not libreoffice_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LibreOffice is not installed on this server.",
            )
        return Response(
            media_type="application/pdf",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": _frame_ancestors(),
            },
        )
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="This file type has no PDF preview.",
    )


async def _render_preview_pdf(doc_id: str) -> FileResponse:
    record, path = _record_and_file(doc_id)

    if path.suffix.lower() == ".pdf":
        rendered = path
    elif is_office(path):
        try:
            rendered = convert_locked(path, doc_id)
        except ConversionUnavailable as exc:
            log.info("Office preview unavailable for %s: %s", doc_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="This file type has no PDF preview.",
        )

    return FileResponse(
        rendered,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _frame_ancestors(),
        },
    )


@router.get("/{doc_id}/preview", response_model=DocumentPreviewOut)
async def preview_document(
    doc_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(_PREVIEW_PAGE_SIZE, ge=1, le=300),
) -> DocumentPreviewOut:
    """The text actually indexed for a document, in reading order.

    Deliberately serves chunks, not the original file. Uploaded files are
    deleted right after ingestion, so the index holds the only surviving copy
    of the content — and previewing *that* is what the user wants to verify
    anyway: whether the extraction is clean enough to trust in an answer.

    A document that exists in the catalogue but has no chunks (a failed or
    pruned ingest) reports empty rather than 404, so the UI can show the
    metadata it already has and an honest "nothing to preview".
    """
    index = get_index()
    passages = index.document_passages(doc_id, offset=offset, limit=limit)
    total = index.document_chunk_count(doc_id)

    if not passages and total == 0:
        known = {str(record["doc_id"]) for record in _catalog().list()}
        if doc_id not in known:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
            )

    record = next(
        (r for r in _catalog().list() if str(r["doc_id"]) == doc_id), None
    )
    return DocumentPreviewOut(
        doc_id=doc_id,
        source=str(record["source"]) if record else doc_id,
        doc_type=str(
            (record or {}).get("doc_type") or doc_type_for(str((record or {}).get("source", "")))
        ),
        pages=int((record or {}).get("pages") or 0),
        chunks=total,
        offset=offset,
        items=[
            PreviewChunkOut(
                index=offset + position,
                page=passage.page,
                locator=passage.label,
                text=passage.text,
            )
            for position, passage in enumerate(passages)
        ],
        has_more=offset + len(passages) < total,
    )
