from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.core.security import SlidingWindowRateLimiter
from app.models.schemas import AskRequest, AskResponse, CitationOut
from app.services.gemini_provider import (
    ProviderUnavailableError,
    RateLimitError,
    TransientError,
    get_provider,
)
from app.services.retrieval_service import RetrievalResult, retrieve

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

_limiter = SlidingWindowRateLimiter(limit=get_settings().rate_limit_per_minute)

NO_CONTEXT_ANSWER = (
    "I couldn't find anything about that in the documents you've uploaded. "
    "Try rephrasing, or upload a document that covers the topic."
)


def _client_key(request: Request) -> str:
    """Best-effort client key for the rate limiter.

    Honours `X-Forwarded-For` when the app is behind a trusted proxy, falls
    back to the socket peer otherwise. Set `TRUST_FORWARDED_FOR=1` to trust
    the header; otherwise attackers can spoof it.
    """
    trust = getattr(get_settings(), "trust_forwarded_for", False)
    if trust:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "anonymous"


def _retrieve_or_400(request: AskRequest):
    try:
        return retrieve(request.question, doc_ids=request.doc_ids)
    except ProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


async def _retrieve(request: AskRequest) -> RetrievalResult:
    """Run retrieval off the event loop.

    `retrieve` is synchronous and spends most of its time waiting on the
    embedding API. Calling it directly from an `async def` route froze the
    whole server for that duration — one question in flight stalled every
    other request, including the `/file` HEAD probes the preview dialog fires
    while an answer is streaming. A thread keeps the loop responsive, and
    Chroma's own locking already makes concurrent retrieval safe.
    """
    return await run_in_threadpool(_retrieve_or_400, request)


@router.post("/ask", response_model=AskResponse)
async def ask(request: Request, payload: AskRequest) -> AskResponse:
    _limiter.check(_client_key(request))
    settings = get_settings()
    t0 = time.perf_counter()
    result = await _retrieve(payload)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not result.grounded:
        log.info(
            "ask not_grounded question=%r retrieval_ms=%.1f",
            payload.question[:80], retrieval_ms,
        )
        return AskResponse(
            answer=NO_CONTEXT_ANSWER,
            citations=[],
            grounded=False,
            interaction_id=None,
            model=settings.chat_model,
        )

    t0 = time.perf_counter()
    try:
        answer, interaction_id = get_provider().answer(
            question=payload.question,
            context=result.context,
            previous_interaction_id=payload.previous_interaction_id,
        )
    except ProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except RateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Provider rate-limited: {exc}",
        ) from exc
    except TransientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Provider transient error: {exc}",
        ) from exc
    generation_ms = (time.perf_counter() - t0) * 1000

    log.info(
        "ask grounded citations=%d retrieval_ms=%.1f generation_ms=%.1f",
        len(result.citations), retrieval_ms, generation_ms,
    )

    return AskResponse(
        answer=answer,
        citations=[CitationOut(**c.as_dict()) for c in result.citations],
        grounded=True,
        interaction_id=interaction_id,
        model=settings.chat_model,
    )


@router.post("/ask/stream")
async def ask_stream(request: Request, payload: AskRequest) -> StreamingResponse:
    """Server-sent events: one `citations` frame, then `delta` frames, then `done`.

    Cancellation: as soon as the client disconnects, the cancel event is
    signalled so the underlying Gemini stream stops on the next SDK
    iteration. The provider's iterator runs in a worker thread that pushes
    text frames into a bounded queue; the async generator consumes from
    that queue while polling `request.is_disconnected()` between yields.
    """
    _limiter.check(_client_key(request))
    settings = get_settings()
    cancel_event = threading.Event()

    try:
        result = await _retrieve(payload)
    except ProviderUnavailableError as exc:
        return _sse_error(str(exc))

    def emit(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    if not result.grounded:
        async def empty() -> AsyncIterator[str]:
            yield emit("delta", {"text": NO_CONTEXT_ANSWER})
            yield emit("done", {"grounded": False, "citations": []})

        return StreamingResponse(
            empty(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    citations_frame = emit(
        "citations",
        {
            "grounded": True,
            "citations": [c.as_dict() for c in result.citations],
            "top_score": result.top_score,
        },
    )

    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue(maxsize=32)
    captured: dict[str, str | None] = {"interaction_id": None}

    def _put(item: tuple[str, str | None]) -> None:
        """Best-effort enqueue from the worker thread.

        The consumer can disappear at any moment (client abort), and once it
        does the queue stops draining. A bare `put_nowait` on a full queue
        raises, and raising out of the worker's `except` / `finally` kills the
        thread with an unhandled exception.
        """
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            cancel_event.set()

    def _drain() -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _pump_from_provider() -> None:
        """Run the provider iterator and push text into the asyncio queue.

        Runs in a worker thread; never touches the loop directly.
        """
        try:
            for text in get_provider().answer_stream(
                question=payload.question,
                context=result.context,
                previous_interaction_id=payload.previous_interaction_id,
                on_interaction_id=lambda i: captured.update(interaction_id=i),
                cancel_event=cancel_event,
            ):
                while not cancel_event.is_set():
                    try:
                        queue.put_nowait(("delta", text))
                        break
                    except asyncio.QueueFull:
                        time.sleep(0.01)
                if cancel_event.is_set():
                    break
        except Exception as exc:  
            log.exception("Streaming failed")
            _put(("error", str(exc)))
        finally:
            _put(("done", None))

    thread = threading.Thread(target=_pump_from_provider, daemon=True)
    thread.start()

    async def stream() -> AsyncIterator[str]:
        try:
            yield citations_frame
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, payload_v = await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
                if kind == "delta" and payload_v is not None:
                    yield emit("delta", {"text": payload_v})
                elif kind == "error":
                    yield emit("error", {"message": payload_v or "stream failed"})
                elif kind == "done":
                    break

            yield emit(
                "done",
                {
                    "grounded": True,
                    "model": settings.chat_model,
                    "interaction_id": captured["interaction_id"],
                    "cancelled": cancel_event.is_set(),
                },
            )
        finally:
            cancel_event.set()
            _drain()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_error(message: str) -> StreamingResponse:
    payload = f"event: error\ndata: {json.dumps({'message': message})}\n\n"
    return StreamingResponse(iter([payload]), media_type="text/event-stream")

@router.get("/metrics/retrieval", response_model=dict)
async def retrieval_metrics() -> dict:
    settings = get_settings()
    if not settings.enable_metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Metrics are disabled"
        )
    from app.services.retrieval_service import recent_stats
    return recent_stats()