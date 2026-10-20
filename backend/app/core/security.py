from __future__ import annotations

import hmac
import re
import time
import unicodedata
from collections import defaultdict, deque
from pathlib import Path

from fastapi import HTTPException, Request, status

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_DOT = re.compile(r"\.{2,}")


def sanitize_filename(filename: str | None, *, fallback: str = "document.pdf") -> str:
    """Reduce an arbitrary client filename to a safe, single-segment basename.

    Strips any directory component, neutralises traversal sequences, drops
    non-ASCII, and caps the length while preserving the extension. A name with
    no extension is returned as-is rather than being given a made-up one — the
    upload endpoint rejects it with a message explaining why, which beats
    silently filing a `.docx` under `.pdf`.
    """
    raw = Path(filename or fallback).name  
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    raw = _UNSAFE.sub("_", raw)
    raw = _MULTI_DOT.sub(".", raw).strip("._")

    if not raw:
        return fallback

    stem, dot, ext = raw.rpartition(".")
    if not dot: 
        return raw[:105]
    if len(ext) > 10:  
        return raw[:105]

    stem = (stem or fallback)[:100]
    return f"{stem}.{ext}"


def display_filename(filename: str | None, *, fallback: str = "document") -> str:
    """Basename for UI display and citation attribution.

    `sanitize_filename` is for the filesystem and strips every non-ASCII
    character, which would mangle "rapport-énoncé.pdf" in the sidebar. This
    keeps the human-readable name but drops any directory component, so a
    hostile `../../../etc/passwd` can never be shown — or cited — as though it
    were a real document name.
    """
    raw = Path(filename or fallback).name.strip()
    if not raw or raw in {".", ".."}:
        return fallback
    return raw[:160]


def resolve_within(base_dir: Path, filename: str) -> Path:
    """Join and assert the result stays inside `base_dir`.

    Belt-and-braces: even if sanitisation were bypassed, this raises.
    """
    base = base_dir.resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )
    return target


def check_signature(head: bytes, filename: str) -> None:
    """Reject a file whose content contradicts its extension.

    Delegates to the format registry so signature rules live in exactly one
    place. Text formats have no magic bytes and are always accepted — their
    loader is the real gate.
    """
    from app.services.loaders import LoadError
    from app.services.loaders import check_signature as _check

    try:
        _check(head, Path(filename).suffix)
    except LoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def check_pdf_signature(head: bytes) -> None:
    """Deprecated PDF-only wrapper kept for existing callers and tests.

    Prefer `check_signature(head, filename)`, which handles every format.
    """
    check_signature(head, "upload.pdf")


async def enforce_api_key(
    request: Request,
    api_key: str = "",
) -> None:
    """FastAPI dependency that rejects requests without a valid bearer token.

    When `api_key` is empty the dependency is a no-op (the original dev
    experience). When set, every request must include an exact `Bearer` match.
    Comparison is `hmac.compare_digest` so timing analysis can't extract the
    key character by character.
    """
    if not api_key:
        return
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = header[7:].strip()
    if not hmac.compare_digest(presented, api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token.",
        )


class SlidingWindowRateLimiter:
    """In-process fixed-window limiter.

    Adequate for a single-worker deployment. Behind multiple workers or
    replicas this becomes per-worker rather than global — swap for Redis if
    you ever scale horizontally.
    """

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if len(bucket) >= self._limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Slow down.",
            )
        bucket.append(now)