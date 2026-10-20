from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_ACCENT_GLITCHES: tuple[tuple[str, str, str], ...] = (
    # Uppercase first (longer matches).
    ("E", "\u00B4", "É"),
    ("E", "`", "È"),
    ("E", "^", "Ê"),
    ("E", "\u02C6", "Ê"),
    ("E", "\u00A8", "Ë"),
    ("A", "\u00B4", "Á"),
    ("A", "`", "À"),
    ("A", "^", "Â"),
    ("A", "\u02C6", "Â"),
    ("A", "\u00A8", "Ä"),
    ("I", "\u00B4", "Í"),
    ("I", "`", "Ì"),
    ("I", "^", "Î"),
    ("I", "\u02C6", "Î"),
    ("I", "\u00A8", "Ï"),
    ("O", "\u00B4", "Ó"),
    ("O", "`", "Ò"),
    ("O", "^", "Ô"),
    ("O", "\u02C6", "Ô"),
    ("O", "\u00A8", "Ö"),
    ("U", "\u00B4", "Ú"),
    ("U", "`", "Ù"),
    ("U", "^", "Û"),
    ("U", "\u02C6", "Û"),
    ("U", "\u00A8", "Ü"),
    ("Y", "\u00B4", "Ý"),
    # Lowercase.
    ("e", "\u00B4", "é"),
    ("e", "`", "è"),
    ("e", "^", "ê"),
    ("e", "\u02C6", "ê"),
    ("e", "\u00A8", "ë"),
    ("a", "\u00B4", "á"),
    ("a", "`", "à"),
    ("a", "^", "â"),
    ("a", "\u02C6", "â"),
    ("a", "\u00A8", "ä"),
    ("i", "\u00B4", "í"),
    ("i", "`", "ì"),
    ("i", "^", "î"),
    ("i", "\u02C6", "î"),
    ("i", "\u00A8", "ï"),
    ("o", "\u00B4", "ó"),
    ("o", "`", "ò"),
    ("o", "^", "ô"),
    ("o", "\u02C6", "ô"),
    ("o", "\u00A8", "ö"),
    ("u", "\u00B4", "ú"),
    ("u", "`", "ù"),
    ("u", "^", "û"),
    ("u", "\u02C6", "û"),
    ("u", "\u00A8", "ü"),
    ("y", "\u00B4", "ý"),
    # Cedillas (the PDF stores the spacing cedilla U+00B8 next to c/C).
    ("C", "\u00B8", "Ç"),
    ("c", "\u00B8", "ç"),
)
_ACCENT_MAP: dict[str, str] = {
    f"{letter}{mod}": repl
    for letter, mod, repl in _ACCENT_GLITCHES
}

_NORMALIZE_FORM = "NFKC"


class PdfError(RuntimeError):
    """Raised when a file cannot be opened or contains no usable text."""


@dataclass(frozen=True)
class Page:
    number: int
    text: str


def _fix_accent_glitches(text: str) -> str:
    """Repair `´e`, `` `e ``, `^e`, etc. that PDF encoders split into two code points.

    The PDF text stream stored `é` as the sequence `<e> <U+00B4>` instead
    of the single `<é>` codepoint. PyMuPDF emits the two codepoints
    faithfully, which means every downstream search fails unless we
    reassemble them. The PDF glyph ordering is **modifier first, base
    letter second**, so we look at `text[i+1]` when we encounter a
    modifier — not `text[i-1]`.
    """
    chars = list(text)
    n = len(chars)
    i = 0
    while i < n:
        ch = chars[i]
        if ch in _ACCENT_MODIFIERS and i + 1 < n:
            nxt = chars[i + 1]
            pair = f"{nxt}{ch}"
            if pair in _ACCENT_MAP:
                chars[i + 1] = _ACCENT_MAP[pair]
                chars[i] = ""
                i += 2
                continue
        i += 1
    return "".join(chars)


_ACCENT_MODIFIERS = frozenset({
    "\u00B4",  # acute accent
    "`",        # grave accent (ASCII backtick)
    "^",        # circumflex (ASCII caret)
    "\u02C6",   # circumflex (spacing modifier, what PyMuPDF often emits)
    "\u00A8",   # diaeresis
    "\u00B8",   # cedilla
})


def _clean(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _fix_accent_glitches(text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    text = unicodedata.normalize(_NORMALIZE_FORM, text)
    return text.strip()


def extract_pages(pdf_path: Path, *, max_pages: int) -> list[Page]:
    """Return non-empty pages of cleaned text, 1-indexed.

    Scanned PDFs yield no text layer. We detect that and say so explicitly
    rather than silently indexing nothing, which is the single most common
    "it just doesn't work" report for PDF chatbots.
    """
    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc: 
        raise PdfError(f"Could not open PDF: {exc}") from exc

    try:
        if document.needs_pass:
            raise PdfError("This PDF is password-protected.")

        pages: list[Page] = []
        for index, page in enumerate(document, start=1):
            if index > max_pages:
                break
            text = _clean(page.get_text())
            if text:
                pages.append(Page(number=index, text=text))
    finally:
        document.close()

    if not pages:
        raise PdfError(
            "No extractable text found. This looks like a scanned PDF — "
            "it needs OCR before it can be indexed."
        )
    return pages


def count_pages(pdf_path: Path) -> int:
    try:
        with pymupdf.open(pdf_path) as document:
            return document.page_count
    except Exception:  
        return 0
