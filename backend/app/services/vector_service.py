from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

import chromadb
from rank_bm25 import BM25Okapi

from app.config import get_settings

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_CAMEL_SPLIT = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])"          
    r"|(?<=[A-Z])(?=[A-Z][a-z])"       
)
_HYPHEN_SPLIT = re.compile(r"[_\-./]+")

_COLLECTION = "gemini_rag"


def _normalize_for_tokenization(text: str) -> str:
    """Insert spaces at CamelCase and hyphen boundaries, then lowercase.

    The result is fed to `_TOKEN.findall` which extracts `[a-z0-9]+`
    runs. Doing the pre-processing here means BM25 sees the same tokens
    whether the document said "sw-consulting", "sw consulting",
    "SWConsulting", "SW Consulting", or "sw.consulting" — a single
    concept, multiple common spellings, all indexable.

    CamelCase splitting happens *before* lowercasing so we can detect the
    word boundaries; once lowercased, "SWConsulting" would already be
    "swconsulting" and the boundary would be invisible.
    """
    text = _CAMEL_SPLIT.sub(" ", text)
    text = _HYPHEN_SPLIT.sub(" ", text)
    text = text.lower()
    return text

# Chroma rejects numpy arrays; normalise whatever the SDK hands back.
def _to_list(vector: list[float] | Any) -> list[float]:
    if hasattr(vector, "tolist"):
        return [float(x) for x in vector.tolist()]
    return [float(x) for x in vector]


def tokenize(text: str) -> list[str]:
    """Tokenise text for BM25 with CamelCase and hyphen-aware splitting.

    Examples:
        "sw-consulting"          → ["sw", "consulting"]
        "SWConsulting"           → ["sw", "consulting"]
        "SW Consulting"          → ["sw", "consulting"]
        "PDF/A-2b"               → ["pdf", "a", "2b"]
    """
    return _TOKEN.findall(_normalize_for_tokenization(text))


@dataclass(frozen=True)
class Passage:
    doc_id: str
    source: str
    page: int
    text: str
    # Display locator ("Slide 4", "Rows 10-29"). Restored from Chroma on
    # startup so BM25 citations keep their location after a restart.
    label: str = ""


class DocumentIndex:
    """Thread-safe store for the dense and sparse indexes.

    Chroma is not safe for concurrent writes from multiple threads, so all
    mutations and reads go through a single re-entrant lock. Upload volume here
    is low, so a lock is the right trade against connection-pool complexity.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = threading.RLock()
        self._client = chromadb.PersistentClient(path=str(self._settings.chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._corpus: dict[str, list[Passage]] = {}
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[tuple[str, int]] = []
        self._rebuild_from_chroma()

    def replace_document(
        self,
        *,
        doc_id: str,
        source: str,
        chunks: list[Any],
        embeddings: list[list[float]],
    ) -> int:
        """Atomically swap a document's contents.

        Deleting before inserting means re-uploading an updated file never
        leaves the previous version's chunks polluting retrieval.
        """
        with self._lock:
            self._collection.delete(where={"doc_id": doc_id})
            self._corpus.pop(doc_id, None)

            if not chunks:
                self._rebuild_bm25()
                return 0

            ids = [f"{doc_id}:{chunk.index}" for chunk in chunks]
            self._collection.add(
                ids=ids,
                embeddings=[_to_list(v) for v in embeddings],
                documents=[chunk.text for chunk in chunks],
                metadatas=[
                    {
                        "doc_id": chunk.doc_id,
                        "source": chunk.source,
                        "page": int(chunk.page),
                        "chunk_index": int(chunk.index),
                        "locator": chunk.locator or "",
                    }
                    for chunk in chunks
                ],
            )
            self._corpus[doc_id] = [
                Passage(
                    doc_id=doc_id,
                    source=source,
                    page=chunk.page,
                    text=chunk.text,
                    label=chunk.locator,
                )
                for chunk in chunks
            ]
            self._rebuild_bm25()
            return len(chunks)

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            existing = self._collection.get(where={"doc_id": doc_id}, limit=1)
            self._collection.delete(where={"doc_id": doc_id})
            self._corpus.pop(doc_id, None)
            self._rebuild_bm25()
            return len(existing.get("ids", [])) > 0


    def dense_search(
        self, query_vector: list[float], *, top_k: int, doc_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        where = {"doc_id": {"$in": doc_ids}} if doc_ids else None
        try:
            result = self._collection.query(
                query_embeddings=[_to_list(query_vector)],
                n_results=min(top_k, max(1, self._collection.count())),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc: 
            log.warning("Dense search failed: %s", exc)
            return []

        passages: list[dict[str, Any]] = []
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        for meta, doc, distance in zip(
            metadatas, documents, distances, strict=False
        ):
            if meta is None or doc is None:
                continue
            passages.append(
                {
                    "doc_id": meta.get("doc_id", ""),
                    "source": meta.get("source", ""),
                    "page": int(meta.get("page", 0)),
                    "text": doc,
                    "locator": str(meta.get("locator") or ""),
                    "distance": float(distance),
                    "score": max(0.0, 1.0 - float(distance)),
                    "dense_score": max(0.0, 1.0 - float(distance)),
                }
            )
        return passages

    def sparse_search(
        self, query: str, *, top_k: int, doc_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """BM25 over the in-memory corpus. Returns the same shape as dense."""
        with self._lock:
            if self._bm25 is None or not self._corpus:
                return []
            allowed = set(doc_ids) if doc_ids else None

            # BM25 scores the whole corpus; we mask disallowed docs afterwards.
            scores = self._bm25.get_scores(tokenize(query))
            ranked = sorted(
                ((float(s), i) for i, s in enumerate(scores) if s > 0),
                reverse=True,
            )

            results: list[dict[str, Any]] = []
            for score, position in ranked:
                doc_id, page = self._bm25_ids[position]
                if allowed is not None and doc_id not in allowed:
                    continue
                passage = self._corpus[doc_id][page]
                results.append(
                    {
                        "doc_id": doc_id,
                        "source": passage.source,
                        "page": passage.page,
                        "text": passage.text,
                        "locator": passage.label,
                        "score": score,
                        # Raw BM25, used as the lexical arm of the relevance floor.
                        "bm25": score,
                    }
                )
                if len(results) >= top_k:
                    break
            return results

    def document_count(self) -> int:
        with self._lock:
            return len(self._corpus)

    def has_document(self, doc_id: str) -> bool:
        with self._lock:
            return doc_id in self._corpus

    def doc_ids(self) -> set[str]:
        with self._lock:
            return set(self._corpus)

    def chunk_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._corpus.values())

    def document_chunk_count(self, doc_id: str) -> int:
        with self._lock:
            return len(self._corpus.get(doc_id, ()))

    def document_passages(
        self, doc_id: str, *, offset: int = 0, limit: int = 60
    ) -> list[Passage]:
        """Stored chunks for one document, in reading order.

        This backs the client's content preview. The source file is deleted
        once ingestion finishes (nothing keeps it on disk), so the indexed
        text is the only faithful record of what was read — a preview built
        from it shows the user exactly what the model will answer from,
        which is the whole point of a preview in a grounded RAG app.

        Sliced under the lock and copied out, so a concurrent upload that
        replaces this document can't mutate the list while it serialises.
        """
        with self._lock:
            passages = self._corpus.get(doc_id)
            if not passages:
                return []
            start = max(0, offset)
            return list(passages[start : start + limit])

    def empty(self) -> bool:
        with self._lock:
            return not self._corpus


    def _rebuild_bm25(self) -> None:
        tokens: list[list[str]] = []
        self._bm25_ids = []
        for doc_id, passages in self._corpus.items():
            for position, passage in enumerate(passages):
                tokens.append(tokenize(passage.text))
                self._bm25_ids.append((doc_id, position))
        if not tokens:
            self._bm25 = None
            return
        if any(len(t) == 0 for t in tokens):
            tokens = [t if t else ["\x00empty"] for t in tokens]
        self._bm25 = BM25Okapi(tokens)

    def _rebuild_from_chroma(self) -> None:
        """Repopulate the in-memory corpus from Chroma on startup.

        Critical for survival across restarts. Chroma is the durable store;
        `_corpus` was previously created empty on boot, so a restart would
        silently strip BM25 of every document and would let the catalog
        `reconcile()` step drop every entry on first call. Now we load once.
        """
        try:
            data = self._collection.get(include=["documents", "metadatas"])
        except Exception as exc:  
            log.warning("Could not rebuild in-memory index from Chroma: %s", exc)
            return
        rows: list[tuple[str, int, int, str, str, str]] = []
        for row_id, doc, meta in zip(
            data.get("ids", []), data.get("documents", []), data.get("metadatas", []),
            strict=False,
        ):
            if not meta or doc is None:
                continue
            doc_id = str(meta.get("doc_id") or "")
            source = str(meta.get("source") or "")
            locator = str(meta.get("locator") or "")
            try:
                page = int(meta.get("page") or 0)
            except (TypeError, ValueError):
                page = 0
            if not doc_id:
                continue
            rows.append((doc_id, page, row_id, source, doc, locator))

        rows.sort(key=lambda r: (r[0], r[1]))

        with self._lock:
            self._corpus.clear()
            for doc_id, _page, _row_id, source, doc, locator in rows:
                self._corpus.setdefault(doc_id, []).append(
                    Passage(
                        doc_id=doc_id, source=source, page=_page, text=doc, label=locator
                    )
                )
            # Some Chroma versions report pages out of order; re-key by the order
            # they arrived in sorted order.
            self._rebuild_bm25()
            log.info(
                "Index loaded from disk: %d documents, %d chunks",
                len(self._corpus),
                sum(len(v) for v in self._corpus.values()),
            )


_index: DocumentIndex | None = None
_index_lock = threading.Lock()


def get_index() -> DocumentIndex:
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = DocumentIndex()
    return _index


def reset_index() -> None:
    """Drop the cached instance. For tests that reconfigure DATA_DIR.

    Mirrors `catalog_service.reset_catalog`: the singletons are process-wide,
    so a test that points `DATA_DIR` somewhere else would otherwise keep
    reading the developer's real Chroma store.
    """
    global _index
    with _index_lock:
        _index = None
