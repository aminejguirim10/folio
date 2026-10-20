from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    source: str
    page: int
    index: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Display locator for the unit this chunk came from ("Slide 4",
    # "Rows 10-29"). Empty for callers that pass plain page objects.
    locator: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "page": self.page,
            "chunk_index": self.index,
            "locator": self.locator,
            **self.metadata,
        }


def chunk_pages(
    pages: list[Any],
    *,
    doc_id: str,
    source: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    """Split units into overlapping, sentence-aware chunks.

    `pages` accepts anything with `.number` and `.text` (i.e. `Page` or
    `loaders.Unit`); `.locator`/`.label` is picked up when present and carried
    into the chunk metadata so citations can point at a slide or a row range
    instead of a bare page index. Overlap is capped at half the chunk size so
    the splitter cannot degenerate into emitting the same text forever.
    """
    settings = get_settings()
    size = chunk_size or settings.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
    overlap = max(0, min(overlap, size // 2))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        # Ordered from most to least semantically meaningful boundary, so a
        # chunk prefers to break on a paragraph, then a line, then a sentence.
        separators=["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""],
        length_function=len,
        add_start_index=True,
        strip_whitespace=True,
    )

    chunks: list[Chunk] = []
    index = 0
    for page in pages:
        locator = getattr(page, "label", "") or getattr(page, "locator", "")
        for piece in splitter.split_text(page.text):
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    source=source,
                    page=page.number,
                    index=index,
                    text=piece,
                    locator=locator,
                )
            )
            index += 1
    return chunks
