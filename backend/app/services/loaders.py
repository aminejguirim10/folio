from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class LoadError(RuntimeError):
    """Raised when a file cannot be turned into indexable text."""


@dataclass(frozen=True)
class Unit:
    number: int
    text: str
    label: str = ""


@dataclass(frozen=True)
class LoadResult:
    units: list[Unit]
    doc_type: str
    total_units: int
    capped: bool = False
    confidence: str = ""
    notes: str = ""


@dataclass(frozen=True)
class FormatSpec:
    doc_type: str
    extensions: frozenset[str]
    load: Callable[[Path, int], LoadResult]
    description: str
    requires: str = ""
    signature: tuple[bytes, ...] = ()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _budget() -> int:
    """How much text to pack into one unit before starting a new one."""
    return max(600, get_settings().chunk_size * 2)


def _pack(
    blocks: list[tuple[int, str]],
    *,
    max_units: int,
    label_for: Callable[[int, int], str],
) -> tuple[list[Unit], bool]:
    """Group `(source_index, text)` blocks into units of ~`_budget()` chars.

    `source_index` is whatever the caller wants the label to refer to (a line
    number, a row number, a Word block number). Blank blocks are dropped, and
    the label spans the first and last *kept* index of the group — so the
    locator always points at real content, never at a skipped blank line.

    Returns `(units, capped)`. `capped` is True when collection stopped
    because it reached `max_units`, meaning content was left behind.
    """
    budget = _budget()
    units: list[Unit] = []
    buffer: list[str] = []
    first = last = 0
    size = 0

    for index, text in blocks:
        if not text:
            continue
        if buffer and size + len(text) > budget:
            units.append(Unit(len(units) + 1, "\n".join(buffer), label_for(first, last)))
            if len(units) >= max_units:
                return units, True
            buffer, size = [], 0
        if not buffer:
            first = index
        last = index
        buffer.append(text)
        size += len(text)

    if buffer:
        units.append(Unit(len(units) + 1, "\n".join(buffer), label_for(first, last)))
    return units, len(units) >= max_units


def _span(prefix: str) -> Callable[[int, int], str]:
    """`Lines 1-40` / `Row 7` — singular when the span is a single item."""

    def build(first: int, last: int) -> str:
        if first == last:
            return f"{prefix} {first}"
        return f"{prefix} {first}-{last}"

    return build


def _row_label(start: int, end: int) -> str:
    return f"Row {start}" if start == end else f"Rows {start}-{end}"



def _load_pdf(path: Path, max_units: int) -> LoadResult:
    from app.services.pdf_service import PdfError, count_pages, extract_pages

    try:
        pages = extract_pages(path, max_pages=max_units)
    except PdfError as exc:
        raise LoadError(str(exc)) from exc
    total = count_pages(path)
    return LoadResult(
        units=[Unit(p.number, p.text, f"Page {p.number}") for p in pages],
        doc_type="pdf",
        total_units=max(total, len(pages)),
        capped=total > len(pages),
    )


def _load_plaintext(path: Path, max_units: int) -> LoadResult:
    text = _read_text(path)
    if not text.strip():
        raise LoadError("This file is empty.")
    blocks = [(n, line.strip()) for n, line in enumerate(text.splitlines(), start=1)]
    units, capped = _pack(blocks, max_units=max_units, label_for=_span("Line"))
    if not units:
        raise LoadError("No indexable text found in this file.")
    return LoadResult(units, "txt", len(units), capped)


def _load_csv(path: Path, max_units: int) -> LoadResult:
    raw = _read_text(path)
    if not raw.strip():
        raise LoadError("This CSV is empty.")
    try:
        dialect = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    rows = list(csv.reader(io.StringIO(raw), dialect))
    if not rows:
        raise LoadError("This CSV is empty.")
    header = [c.strip() or f"col{i + 1}" for i, c in enumerate(rows[0])]

    budget = _budget()
    units: list[Unit] = []
    buffer: list[str] = []
    size = 0
    start = 2
    row_no = 1
    capped = False

    for row_no, row in enumerate(rows[1:], start=2):
        pairs = [
            f"{h}: {v.strip()}"
            for h, v in zip(header, row, strict=False)
            if v and v.strip()
        ]
        if not pairs:
            continue
        line = "\n".join(pairs)
        if buffer and size + len(line) > budget:
            units.append(
                Unit(len(units) + 1, "\n".join(buffer), _row_label(start, row_no - 1))
            )
            if len(units) >= max_units:
                capped = True
                break
            buffer, size, start = [], 0, row_no
        if not buffer:
            start = row_no
        buffer.append(line)
        size += len(line)

    if buffer and not capped:
        units.append(Unit(len(units) + 1, "\n".join(buffer), _row_label(start, row_no)))

    if not units:
        raise LoadError("This CSV has a header but no data rows.")
    return LoadResult(units, "csv", len(units), capped)


def _load_html(path: Path, max_units: int) -> LoadResult:
    class _Extractor(HTMLParser):
        SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
        BLOCK = frozenset(
            {
                "p", "div", "section", "article", "br", "li", "tr", "blockquote",
                "pre", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "table",
            }
        )

        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._skip_depth = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in self.SKIP:
                self._skip_depth += 1
            elif tag in self.BLOCK:
                self.parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in self.SKIP and self._skip_depth:
                self._skip_depth -= 1
            elif tag in self.BLOCK:
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip_depth:
                self.parts.append(data)

    parser = _Extractor()
    parser.feed(_read_text(path))
    lines = [re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in "".join(parser.parts).split("\n")]
    blocks = [(n, ln) for n, ln in enumerate(lines, start=1) if ln]
    if not blocks:
        raise LoadError("No visible text found in this HTML file.")
    units, capped = _pack(blocks, max_units=max_units, label_for=_span("Line"))
    if not units:
        raise LoadError("No visible text found in this HTML file.")
    return LoadResult(units, "html", len(units), capped)


def _load_json(path: Path, max_units: int) -> LoadResult:
    try:
        data = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise LoadError(f"Invalid JSON: {exc.msg} (line {exc.lineno}).") from exc

    lines: list[tuple[int, str]] = []
    counter = 0

    def walk(prefix: str, value: Any, depth: int = 0) -> None:
        nonlocal counter
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, sub in value.items():
                if isinstance(sub, (dict, list)):
                    walk(f"{prefix}{key}.", sub, depth + 1)
                else:
                    counter += 1
                    lines.append((counter, f"{prefix}{key}: {sub}"))
        elif isinstance(value, list):
            for position, sub in enumerate(value):
                if isinstance(sub, (dict, list)):
                    walk(f"{prefix}[{position}].", sub, depth + 1)
                else:
                    counter += 1
                    lines.append((counter, f"{prefix}{sub}".rstrip(".")))
        else:
            counter += 1
            lines.append((counter, f"{prefix}{value}".rstrip(".")))

    walk("", data)
    if not lines:
        raise LoadError("This JSON file contains no scalar values to index.")
    units, capped = _pack(lines, max_units=max_units, label_for=_span("Field"))
    if not units:
        raise LoadError("This JSON file contains no scalar values to index.")
    return LoadResult(units, "json", len(units), capped)


def _load_docx(path: Path, max_units: int) -> LoadResult:
    try:
        import docx
        from docx.oxml.ns import qn
    except ImportError as exc:  # pragma: no cover - guarded by supported_formats()
        raise LoadError("python-docx is not installed.") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc: 
        raise LoadError(f"Could not open this Word file: {exc}") from exc

    def blocks() -> Iterator[tuple[int, str]]:
        position = 0
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                position += 1
                yield position, "".join(t.text or "" for t in child.iter(qn("w:t"))).strip()
            elif child.tag == qn("w:tbl"):
                for row in child.iter(qn("w:tr")):
                    position += 1
                    cells = [
                        "".join(t.text or "" for t in cell.iter(qn("w:t"))).strip()
                        for cell in row.iter(qn("w:tc"))
                    ]
                    line = " | ".join(c for c in cells if c)
                    if line:
                        yield position, line

    units, capped = _pack(list(blocks()), max_units=max_units, label_for=_span("Block"))
    if not units:
        raise LoadError("No text found in this Word document.")
    return LoadResult(units, "docx", len(units), capped)


def _load_pptx(path: Path, max_units: int) -> LoadResult:
    try:
        from pptx import Presentation
    except ImportError as exc: 
        raise LoadError("python-pptx is not installed.") from exc

    try:
        presentation = Presentation(str(path))
    except Exception as exc: 
        raise LoadError(f"Could not open this PowerPoint file: {exc}") from exc

    units: list[Unit] = []
    capped = False
    for index, slide in enumerate(presentation.slides, start=1):
        if index > max_units:
            capped = True
            break
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.extend(
                    "".join(run.text for run in paragraph.runs).strip()
                    for paragraph in shape.text_frame.paragraphs
                )
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"Speaker notes: {notes}")
        text = "\n".join(p for p in parts if p)
        if text:
            units.append(Unit(len(units) + 1, text, f"Slide {index}"))

    if not units:
        raise LoadError("No text found in this presentation (only images?).")
    return LoadResult(units, "pptx", len(presentation.slides), capped)


def _load_xlsx(path: Path, max_units: int) -> LoadResult:
    try:
        import openpyxl
    except ImportError as exc:  
        raise LoadError("openpyxl is not installed.") from exc

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise LoadError(f"Could not open this spreadsheet: {exc}") from exc

    budget = _budget()
    units: list[Unit] = []
    capped = False
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            try:
                header_row = next(rows)
            except StopIteration:
                continue
            header = [
                str(cell).strip() if cell is not None else f"col{i + 1}"
                for i, cell in enumerate(header_row)
            ]

            buffer: list[str] = []
            size = 0
            start = 2
            row_no = 1

            def label(a: int, b: int, title: str = sheet.title) -> str:
                span = f"Row {a}" if a == b else f"Rows {a}-{b}"
                return f"Sheet {title} · {span}"

            for row_no, row in enumerate(rows, start=2):
                if not row:
                    continue
                pairs = [
                    f"{name}: {value}"
                    for name, value in zip(header, row, strict=False)
                    if value is not None and str(value).strip()
                ]
                if not pairs:
                    continue
                line = "\n".join(pairs)
                if buffer and size + len(line) > budget:
                    units.append(
                        Unit(len(units) + 1, "\n".join(buffer), label(start, row_no - 1))
                    )
                    if len(units) >= max_units:
                        capped = True
                        break
                    buffer, size, start = [], 0, row_no
                if not buffer:
                    start = row_no
                buffer.append(line)
                size += len(line)

            if buffer:
                units.append(Unit(len(units) + 1, "\n".join(buffer), label(start, row_no)))
            if capped:
                
                break
    finally:
        workbook.close()

    if not units:
        raise LoadError("This spreadsheet has no readable rows.")
    return LoadResult(units, "xlsx", len(units), capped)


def _load_image(path: Path, max_units: int) -> LoadResult:
    """Transcribe an image with the vision model, then index it as one unit.

    This loader is the odd one out: it is the only format that needs the model.
    That is deliberate — an image has no text to extract, and asking the same
    model that will answer questions about the document is both cheaper (no
    extra OCR engine to ship) and more accurate on tables, screenshots and
    handwriting than a local OCR binary.

    The `locator` is "Image" because there is exactly one addressable unit;
    the confidence travels with the result so the UI can warn the user when
    the extraction is not trustworthy.
    """
    from app.services.gemini_provider import get_provider
    from app.services.image_service import ImageError, prepare_image

    try:
        prepared = prepare_image(path)
    except ImageError as exc:
        raise LoadError(str(exc)) from exc

    result = get_provider().transcribe_image(prepared)
    text = result.text.strip()
    if not text:
        raise LoadError(
            "No text could be read from this image. It may be blank, too "
            "blurry, or not a document — a photo of a scene contains nothing "
            "to index."
        )

    log.info(
        "Transcribed image %s via %s: %d chars, confidence=%s%s",
        path.name, result.model, len(text), result.confidence,
        f" ({result.notes})" if result.notes else "",
    )
    return LoadResult(
        units=[Unit(1, text, "Image")],
        doc_type="image",
        total_units=1,
        confidence=result.confidence,
        notes=result.notes,
    )



_SPECS: tuple[FormatSpec, ...] = (
    FormatSpec(
        doc_type="pdf",
        extensions=frozenset({".pdf"}),
        load=_load_pdf,
        description=(
            "PDF documents (embedded text layer; no OCR — scanned PDFs have no text to read)"
        ),
        requires="pymupdf",
        signature=(_PDF_MAGIC,),
    ),
    FormatSpec(
        doc_type="docx",
        extensions=frozenset({".docx"}),
        load=_load_docx,
        description="Word documents, including tables",
        requires="python-docx",
        signature=(_ZIP_MAGIC,),
    ),
    FormatSpec(
        doc_type="pptx",
        extensions=frozenset({".pptx"}),
        load=_load_pptx,
        description="PowerPoint decks, one locator per slide",
        requires="python-pptx",
        signature=(_ZIP_MAGIC,),
    ),
    FormatSpec(
        doc_type="xlsx",
        extensions=frozenset({".xlsx", ".xlsm"}),
        load=_load_xlsx,
        description="Spreadsheets, one locator per sheet and row range",
        requires="openpyxl",
        signature=(_ZIP_MAGIC,),
    ),
    FormatSpec(
        doc_type="image",
        extensions=frozenset(
            {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
        ),
        load=_load_image,
        description="Images transcribed by the vision model (scans, screenshots, photos)",
        requires="pillow",
    ),
    FormatSpec(
        doc_type="csv",
        extensions=frozenset({".csv", ".tsv"}),
        load=_load_csv,
        description="Delimited text, one locator per row range",
    ),
    FormatSpec(
        doc_type="txt",
        extensions=frozenset({".txt", ".md", ".markdown", ".rst", ".log"}),
        load=_load_plaintext,
        description="Plain text, Markdown, reStructuredText and logs",
    ),
    FormatSpec(
        doc_type="html",
        extensions=frozenset({".html", ".htm", ".xhtml"}),
        load=_load_html,
        description="HTML pages (scripts and styles stripped)",
    ),
    FormatSpec(
        doc_type="json",
        extensions=frozenset({".json", ".jsonl"}),
        load=_load_json,
        description="JSON / JSON Lines, flattened to searchable fields",
    ),
)

_BY_EXTENSION: dict[str, FormatSpec] = {
    ext: spec for spec in _SPECS for ext in spec.extensions
}

_KNOWN_UNSUPPORTED: dict[str, str] = {
    ".doc": "Legacy .doc is not supported — re-save it as .docx.",
    ".xls": "Legacy .xls is not supported — re-save it as .xlsx.",
    ".ppt": "Legacy .ppt is not supported — re-save it as .pptx.",
    ".rtf": "RTF is not supported — export to .docx or .txt.",
    ".odt": "OpenDocument .odt is not supported — export to .docx.",
    ".ods": "OpenDocument .ods is not supported — export to .xlsx or .csv.",
}


def required_module_for(spec: FormatSpec) -> str:
    """Import name of the package a spec needs, or "" for stdlib-only."""
    if not spec.requires:
        return ""
    return {
        "docx": "docx",
        "pptx": "pptx",
        "xlsx": "openpyxl",
        "pdf": "pymupdf",
        "image": "PIL",
    }[spec.doc_type]


def _package_available(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  
        return False


def supported_formats() -> list[FormatSpec]:
    """Specs whose optional dependency is actually importable."""
    return [
        s
        for s in _SPECS
        if not s.requires or _package_available(required_module_for(s))
    ]


def all_formats() -> list[FormatSpec]:
    """Every registered spec, available or not, so the UI can list and grey out."""
    return list(_SPECS)


def known_unsupported() -> dict[str, str]:
    """Extensions we recognise but cannot read, mapped to the reason."""
    return dict(_KNOWN_UNSUPPORTED)


def missing_dependency_formats() -> list[FormatSpec]:
    """Specs whose optional parser is not installed in this environment."""
    available = {s.doc_type for s in supported_formats()}
    return [s for s in _SPECS if s.doc_type not in available]


def supported_extensions() -> list[str]:
    return sorted(ext for spec in supported_formats() for ext in spec.extensions)


def spec_for_extension(ext: str) -> FormatSpec | None:
    return _BY_EXTENSION.get(ext.lower())


def doc_type_for(filename: str) -> str:
    """Format tag for a filename, e.g. "pdf", "csv".

    Falls back to the bare extension for anything unregistered so the UI can
    still label a recovered or legacy catalogue entry honestly instead of
    mislabelling every document as a PDF.
    """
    ext = Path(filename or "").suffix.lower()
    spec = _BY_EXTENSION.get(ext)
    return spec.doc_type if spec else (ext.lstrip(".") or "file")


def unsupported_reason(filename: str) -> str:
    """Human-readable explanation when a file cannot be accepted."""
    ext = Path(filename or "").suffix.lower()
    if ext in _KNOWN_UNSUPPORTED:
        return _KNOWN_UNSUPPORTED[ext]
    spec = _BY_EXTENSION.get(ext)
    if spec and spec.requires and not _package_available(required_module_for(spec)):
        return f"{ext} support requires the '{spec.requires}' package, which is not installed."
    if ext:
        return f"{ext} files are not supported."
    return "Files without an extension cannot be identified."


def check_signature(head: bytes, ext: str) -> None:
    """Reject a file whose magic bytes contradict its extension.

    Formats with no meaningful magic (txt, csv, json, html) are accepted: any
    byte sequence is a valid text file, and the loader is the real gate.
    """
    spec = _BY_EXTENSION.get(ext.lower())
    if not spec or not spec.signature:
        return
    if head.startswith(_OLE_MAGIC):
        raise LoadError(
            "This looks like a legacy Office file (OLE format). "
            "Re-save it as .docx, .xlsx or .pptx first."
        )
    if not any(head.startswith(sig) for sig in spec.signature):
        expected = "a PDF" if ext.lower() == ".pdf" else "an Office Open XML file"
        raise LoadError(f"File does not look like {expected}; its content does not match {ext}.")


def load_document(path: Path, filename: str, *, max_units: int) -> LoadResult:
    """Dispatch to the right loader based on the file extension."""
    ext = Path(filename or path.name).suffix.lower()
    spec = spec_for_extension(ext)
    if spec is None:
        raise LoadError(unsupported_reason(filename))
    if spec.requires and not _package_available(required_module_for(spec)):
        raise LoadError(unsupported_reason(filename))
    result = spec.load(path, max_units)
    if not result.units:
        raise LoadError("No indexable content found in this file.")
    return result
