from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()


def record(
    *,
    interaction_id: str | None,
    question: str,
    answer: str,
    rating: int,
    comment: str | None = None,
) -> dict[str, Any]:
    """Append a feedback row. Returns the stored record for the caller."""
    if rating not in (1, -1):
        raise ValueError("rating must be 1 (up) or -1 (down)")
    if comment is not None and len(comment) > 1000:
        comment = comment[:1000]

    settings = get_settings()
    path = settings.data_dir / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "ts": datetime.now(UTC).isoformat(),
        "interaction_id": interaction_id,
        "question": question[:2000],
        "answer": answer[:4000],
        "rating": rating,
        "comment": comment,
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        # fsync isn't always available (e.g. some FUSE filesystems).
        with contextlib.suppress(OSError):
            os.fsync(fh.fileno())
    log.info("feedback stored: rating=%d interaction_id=%s", rating, interaction_id)
    return row


def summary() -> dict[str, Any]:
    """Return up/down counts and the 20 most recent feedback entries."""
    settings = get_settings()
    path = settings.data_dir / "feedback.jsonl"
    if not path.exists():
        return {"up": 0, "down": 0, "total": 0, "recent": []}
    up = down = 0
    recent: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("rating") == 1:
                up += 1
            elif row.get("rating") == -1:
                down += 1
            recent.append(row)
    return {
        "up": up,
        "down": down,
        "total": up + down,
        "recent": recent[-20:],
    }