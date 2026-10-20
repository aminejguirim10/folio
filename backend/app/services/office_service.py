from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

_CONVERT_TIMEOUT_SECONDS = 120

_OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".xlsm"}

_SOFFICE_CANDIDATES = (
    "soffice",
    "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
)


class ConversionUnavailable(RuntimeError):
    """LibreOffice is not installed, or conversion failed."""


def is_office(path: Path) -> bool:
    return path.suffix.lower() in _OFFICE_SUFFIXES


def find_soffice() -> str | None:
    """Locate the LibreOffice binary, honouring `SOFFICE_PATH` first.

    The env override exists because the binary is not always on PATH — most
    notably on Windows, where it lives under Program Files — and because it
    makes the service testable without installing 350 MB.
    """
    override = os.environ.get("SOFFICE_PATH", "").strip()
    if override:
        return override if Path(override).exists() else None
    for candidate in _SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("soffice") or shutil.which("libreoffice")


def libreoffice_available() -> bool:
    return find_soffice() is not None


def cache_dir() -> Path:
    return get_settings().data_dir / "converted"


def cached_pdf(doc_id: str) -> Path | None:
    """A previously converted PDF, if one exists.

    Named after the content-addressed `doc_id`, so it is invalidated by the
    same change that invalidates the index: new bytes, new id, new file.
    """
    candidate = cache_dir() / f"{doc_id}.pdf"
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def convert_to_pdf(source: Path, doc_id: str) -> Path:
    """Convert an Office file to PDF, caching the result.

    Raises `ConversionUnavailable` — never a bare subprocess error — so the
    caller has one thing to catch and one honest message to show.
    """
    existing = cached_pdf(doc_id)
    if existing is not None:
        return existing

    soffice = find_soffice()
    if soffice is None:
        raise ConversionUnavailable("LibreOffice is not installed on this server.")

    out_dir = cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--invisible",
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        try:
            completed = subprocess.run(  
                cmd,
                capture_output=True,
                timeout=_CONVERT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionUnavailable(
                "Conversion timed out; the document may be very large."
            ) from exc
        except OSError as exc:
            raise ConversionUnavailable(
                f"Could not run LibreOffice: {exc}"
            ) from exc

    produced = out_dir / f"{source.stem}.pdf"
    if completed.returncode != 0 or not produced.is_file():
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()[:200]
        log.warning(
            "LibreOffice could not convert %s (exit %s): %s",
            source.name,
            completed.returncode,
            detail or "no output file",
        )
        raise ConversionUnavailable("This document could not be converted to PDF.")

    final = out_dir / f"{doc_id}.pdf"
    shutil.move(str(produced), str(final))
    return final


_conversion_lock = threading.Lock()


def convert_locked(source: Path, doc_id: str) -> Path:
    """Serialise conversions.

    Two simultaneous previews of two different Office files would otherwise
    race on the shared output directory and could pick up each other's
    intermediate file.
    """
    with _conversion_lock:
        return convert_to_pdf(source, doc_id)