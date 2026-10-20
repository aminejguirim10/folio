from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path

from app.config import get_settings
from app.core.security import resolve_within, sanitize_filename

log = logging.getLogger(__name__)

_TEXTUAL_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".htm", ".html"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


_HASH_CHUNK = 1024 * 1024


class StoredFileMissing(RuntimeError):
    """The original file for a document is not on disk any more."""


def _hash16(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()[:16]


def _candidate_names(doc_id: str, stored_as: str, source: str) -> list[str]:
    """Names to try, most trustworthy first.

    `stored_as` is written by the upload handler and is exact. The
    `sanitize_filename(source)` reconstruction recovers documents uploaded
    before that field existed, and is verified by the hash check regardless.
    """
    names: list[str] = []
    for name in (stored_as, sanitize_filename(source, fallback=f"{doc_id}.bin")):
        if name and name not in names:
            names.append(name)
    return names


def resolve_stored_file(doc_id: str, *, stored_as: str = "", source: str = "") -> Path:
    """The verified on-disk path for a document, or raise `StoredFileMissing`.

    Raises rather than returning None so a caller cannot accidentally serve a
    missing file as an empty 200 — the distinction between "no original file"
    and "an empty one" matters to the UI.
    """
    settings = get_settings()
    upload_dir = settings.upload_dir

    for name in _candidate_names(doc_id, stored_as, source):
        try:
            candidate = resolve_within(upload_dir, name)
        except Exception:  
            continue
        if not candidate.is_file():
            continue
        if _hash16(candidate) != doc_id:
            log.warning(
                "Skipping %s for doc %s: content hash does not match", name, doc_id
            )
            continue
        return candidate

    raise StoredFileMissing(f"No stored file matches doc_id {doc_id}.")


def media_type_for(path: Path) -> str:
    """Content type for the stored file, with the common formats pinned.

    `mimetypes` is platform-dependent (a Windows box maps `.md` and `.jsonl`
    differently from a container), and an unrecognised extension silently
    becomes `application/octet-stream`, which the browser refuses to render.
    """
    explicit = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".markdown": "text/plain; charset=utf-8",
        ".csv": "text/plain; charset=utf-8",
        ".tsv": "text/plain; charset=utf-8",
        ".json": "text/plain; charset=utf-8",
        ".jsonl": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
    }
    suffix = path.suffix.lower()
    if suffix in explicit:
        return explicit[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def preview_kind_for(path: Path) -> str:
    """How the client should render this file.

    One of `pdf`, `image`, `text`, `none`. `none` means "no browser renderer"
    and tells the UI to fall back to the extracted text — it must never be
    treated as an error, because the document itself is perfectly fine.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _TEXTUAL_SUFFIXES:
        return "text"
    return "none"