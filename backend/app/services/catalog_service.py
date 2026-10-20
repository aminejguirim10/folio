from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.loaders import doc_type_for

log = logging.getLogger(__name__)


class DocumentCatalog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._records = {}
                return
            try:
                data = json.loads(self._path.read_text("utf-8"))
                self._records = data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not read document catalogue, starting empty: %s", exc)
                self._records = {}

    def reconcile(self, known_doc_ids: set[str]) -> int:
        """Drop records whose vectors are missing. Returns how many were removed."""
        with self._lock:
            before = len(self._records)
            self._records = {
                doc_id: record
                for doc_id, record in self._records.items()
                if doc_id in known_doc_ids
            }
            removed = before - len(self._records)
            if removed:
                log.warning("Pruned %d catalogue entries with no vectors", removed)
                self._flush()
            return removed

    def resurrect(self, known_doc_ids: set[str]) -> int:
        """Re-add catalogue entries for documents present in the index but
        missing from the catalogue.

        Used at startup when the catalogue file was lost (volume restored
        without it) but Chroma still holds every chunk. Returns the number of
        entries recovered.

        Counts `chunks` and the *distinct page count* from the stored
        metadata — that's the best we can do without re-reading the PDF.
        Pages the operator cares about are usually covered by the metadata,
        so a recovered document shows up with sane numbers even though we
        haven't re-parsed the file.
        """
        from app.services.vector_service import get_index  # local to avoid cycle

        if not known_doc_ids:
            return 0
        with self._lock:
            missing = known_doc_ids - set(self._records.keys())
            if not missing:
                return 0
            index = get_index()
            recovered = 0
            now = datetime.now(UTC).isoformat()
            for doc_id in missing:
                try:
                    sample = index._collection.get(  
                        where={"doc_id": doc_id},
                        include=["metadatas"],
                    )
                except Exception: 
                    sample = {"metadatas": [], "ids": []}
                metas = [m for m in (sample.get("metadatas") or []) if m]
                ids = sample.get("ids") or []
                source = metas[0].get("source") if metas else "(recovered)"
                try:
                    pages = max((int(m.get("page") or 0) for m in metas), default=0)
                except (TypeError, ValueError):
                    pages = 0
                self._records[doc_id] = {
                    "doc_id": doc_id,
                    "source": str(source),
                    "doc_type": doc_type_for(str(source)),
                    "pages": pages,
                    "chunks": len(ids),
                    "indexed_at": now,
                }
                recovered += 1
            if recovered:
                log.info(
                    "Recovered %d catalogue entries from the vector store",
                    recovered,
                )
                self._flush()
            return recovered

    def record(
        self,
        *,
        doc_id: str,
        source: str,
        pages: int,
        chunks: int,
        doc_type: str = "pdf",
        confidence: str = "",
        notes: str = "",
        stored_as: str = "",
    ) -> dict[str, Any]:
        entry = {
            "doc_id": doc_id,
            "source": source,
            "doc_type": doc_type,
            "pages": pages,
            "chunks": chunks,
            "confidence": confidence,
            "notes": notes,
            "stored_as": stored_as,
            "indexed_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._records[doc_id] = entry
            self._flush()
        return entry

    def remove(self, doc_id: str) -> None:
        with self._lock:
            if self._records.pop(doc_id, None) is not None:
                self._flush()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records.values())

    def clear(self) -> None:
        with self._lock:
            self._records = {}
            self._flush()

    def _flush(self) -> None:
        """Atomic write: a crash mid-save must not corrupt the catalogue."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".catalog-", suffix=".json"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(self._records, fh, indent=2)
            os.replace(tmp_name, self._path)
        except OSError as exc:
            log.warning("Could not persist document catalogue: %s", exc)
            Path(tmp_name).unlink(missing_ok=True)


_catalog: DocumentCatalog | None = None
_catalog_lock = threading.Lock()


def get_catalog() -> DocumentCatalog:
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                from app.config import get_settings

                _catalog = DocumentCatalog(get_settings().data_dir / "catalog.json")
    return _catalog


def reset_catalog() -> None:
    """Drop the cached instance. For tests that reconfigure DATA_DIR."""
    global _catalog
    with _catalog_lock:
        _catalog = None
