from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai.errors import APIError, ClientError, ServerError
from google.genai.types import EmbedContentConfig

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_EMBED_CHARS = 6_000

_MAX_HISTORY_TURNS = 6
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_S = 0.8
_MAX_BACKOFF_S = 8.0


class ProviderUnavailableError(RuntimeError):
    """Raised when the Gemini client cannot be constructed (e.g. no API key)."""


class RateLimitError(RuntimeError):
    """Raised when the provider returns 429 / RESOURCE_EXHAUSTED."""


class TransientError(RuntimeError):
    """Raised for retriable failures (5xx, network timeouts)."""


@dataclass(frozen=True)
class Citation:
    """A single retrieved passage, attributed back to its source page."""

    doc_id: str
    source: str
    page: int
    score: float
    snippet: str
    locator: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "page": self.page,
            "score": round(self.score, 4),
            "snippet": self.snippet,
            "locator": self.locator or f"Page {self.page}",
        }


def _is_transient(exc: BaseException) -> bool:
    """Classify an SDK exception as transient (worth retrying)."""
    if isinstance(exc, (ServerError, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, ClientError):
        status = getattr(exc, "code", None) or getattr(exc, "status", None)
        if status in {408, 425, 429, 500, 502, 503, 504}:
            return True
    if isinstance(exc, APIError):
        status = getattr(exc, "code", None)
        if status in {408, 425, 429, 500, 502, 503, 504}:
            return True
    return False


def _retry(
    op: Callable[[], Any],
    *,
    label: str,
    attempts: int = _MAX_ATTEMPTS,
) -> Any:
    """Run `op()` with exponential backoff + jitter on transient errors."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except Exception as exc: 
            if isinstance(exc, RateLimitError) or not _is_transient(exc):
                raise
            last_exc = exc
            if attempt >= attempts:
                break
            sleep_s = min(_MAX_BACKOFF_S, _BASE_BACKOFF_S * (2 ** (attempt - 1)))
            sleep_s *= 0.5 + random.random()
            log.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, attempts, exc, sleep_s,
            )
            time.sleep(sleep_s)
    raise TransientError(f"{label} failed after {attempts} attempts: {last_exc}")



_CONVERSATION_HISTORY: dict[str, list[dict[str, str]]] = {}
_HISTORY_LOCK = threading.Lock()


def _history_key(previous_interaction_id: str | None) -> str:
    """Map `None` to a stable sentinel per process so the first turn in a
    fresh conversation gets its own bucket."""
    return previous_interaction_id or "__init__"


@dataclass(frozen=True)
class Transcription:
    """Result of turning one image into indexable text.

    `confidence` is the model's own read of how legible the image was, then
    **overridden by deterministic checks** — an empty or implausibly short
    transcription is downgraded to `low` no matter what the model claimed. A
    fluent hallucination over a blank page is the worst outcome here, because
    it would be indexed and cited as if it were real.
    """

    text: str
    confidence: str 
    notes: str = ""
    model: str = ""


def _calibrate(text: str, confidence: str, min_chars: int) -> str:
    """Deterministic guardrail on top of the model's self-assessment.

    The model's confidence is a self-report and is not trustworthy on its own.
    This can only *lower* it, never raise it:

    * empty text → `low` (there is nothing to index)
    * shorter than `min_chars` → `low` (too thin to cite with any confidence)
    * model said `low` → stays `low`

    The caller is expected to surface a `low` result to the user rather than
    let it masquerade as a reliable extraction.
    """
    stripped = text.strip()
    if not stripped:
        return "low"
    if len(stripped) < max(10, min_chars // 3):
        return "low"
    if len(stripped) < min_chars and confidence == "high":
        return "medium"
    return confidence


_TRANSCRIBE_PROMPT = """\
You are a precise document transcription engine.

Transcribe ALL readable text from this image, in reading order, preserving the
document's structure:
- Use markdown for headings, bold, lists and tables.
- For a table, emit a real markdown table with a header row.
- Preserve the original language verbatim. Do NOT translate.
- Do NOT summarise, explain, correct or comment.
- If part of the image is a chart, diagram or photo with no text, describe it
  in one short bracketed line, e.g. "[bar chart: revenue 2020-2024]".

Then rate how confident you are that the transcription is faithful:
- "high"   : text is sharp and fully legible.
- "medium" : readable, but some words are inferred from context, or the image
            is blurry, skewed, or has low contrast.
- "low"    : large parts are cut off, pixelated, handwritten and unclear, or
            the image is not a document at all.

Be conservative: prefer "medium" or "low" over overstating your certainty.

Return ONLY a JSON object, no prose and no code fence:
{"text": "<transcription>", "confidence": "high|medium|low", "notes": "<one short sentence on any difficulty, else empty string>"}
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_transcription(raw: str) -> tuple[str, str, str]:
    """Pull `(text, confidence, notes)` out of the model's JSON reply.

    Tolerates the usual drift: a code fence, leading prose, or outright
    invalid JSON — in which case we fall back to treating the whole reply as
    the transcription rather than losing it.
    """
    candidate = raw.strip()
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        try:
            payload = json.loads(_FENCE_RE.sub("", match.group(0)))
            if isinstance(payload, dict) and "text" in payload:
                confidence = str(payload.get("confidence") or "").lower().strip()
                if confidence not in {"high", "medium", "low"}:
                    confidence = "medium"
                return (
                    str(payload.get("text") or ""),
                    confidence,
                    str(payload.get("notes") or ""),
                )
        except json.JSONDecodeError:
            pass
    return _FENCE_RE.sub("", candidate).strip(), "low", "Response was not valid JSON."


def _generation_models() -> list[str]:
    """Models to try for a chat turn, best first.

    A second model is not a nicety: measured on this project, the configured
    `gemini-3.5-flash` sat behind a "high demand" 503 and, once it did accept
    the request, took 86 seconds to emit its first token. The same question on
    `gemini-2.5-flash` took 1.2 seconds. When the primary is unavailable we
    want to *switch*, not to spend twenty seconds backing off and retrying a
    model that is already telling us it is saturated.
    """
    settings = get_settings()
    models = [settings.chat_model]
    fallback = (settings.chat_fallback_model or "").strip()
    if fallback and fallback != models[0]:
        models.append(fallback)
    return [m for m in models if m]


def _should_failover(exc: BaseException) -> bool:
    """Is this failure worth trying a different model for?

    `_retry` re-raises its own `TransientError` once attempts run out, so a
    saturated model reaches the failover check already wrapped and no longer
    matches `_is_transient` — without this the non-streaming path would give
    up on the first model instead of switching.

    A quota error is deliberately excluded: rate limits are per account, not
    per model, so a second model would hit the same wall one round-trip later.
    """
    if isinstance(exc, TransientError):
        return True
    if isinstance(exc, RateLimitError):
        return False
    return _is_transient(exc)


def _generation_error(exc: BaseException) -> Exception:
    """Map an SDK failure onto the error type the API layer expects."""
    if _is_transient(exc) and getattr(exc, "code", None) in (429,):
        return RateLimitError(_friendly_error(exc, "rate limit"))
    if _is_transient(exc):
        return TransientError(_friendly_error(exc, "transient"))
    return RuntimeError(_friendly_error(exc, "generation"))


class GeminiProvider:
    """Thin, well-scoped wrapper over the Gemini SDK."""

    def __init__(self) -> None:
        self._settings = get_settings()
        if not self._settings.gemini_api_key:
            raise ProviderUnavailableError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self._client = genai.Client(api_key=self._settings.gemini_api_key)


    def transcribe_image(self, image: Any) -> Transcription:
        """Turn a `PreparedImage` into text, with a calibrated confidence.

        Retries transient failures like every other call. The returned
        confidence is post-processed by `_calibrate`, which can only lower it.
        """
        model = self._settings.resolved_vision_model

        def _do() -> Any:
            return self._client.models.generate_content(
                model=model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": _TRANSCRIBE_PROMPT},
                            image.as_inline_part(),
                        ],
                    }
                ],
                config={"temperature": 0.0, "response_mime_type": "application/json"},
            )

        response = _retry(_do, label="Image transcription")
        text, confidence, notes = _parse_transcription(getattr(response, "text", "") or "")
        return Transcription(
            text=text,
            confidence=_calibrate(text, confidence, self._settings.min_transcription_chars),
            notes=notes,
            model=model,
        )


    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed passages for indexing, batched to respect API limits."""
        vectors: list[list[float]] = []
        batch_size = self._settings.embedding_batch_size

        for start in range(0, len(texts), batch_size):
            batch = [
                text[:MAX_EMBED_CHARS] for text in texts[start : start + batch_size]
            ]
            if not batch:
                continue

            # `batch` is bound as a default so the closure keeps this
            # iteration's slice; `_retry` runs `_do` before the loop advances.
            def _do(batch: list[str] = batch) -> Any:
                return self._client.models.embed_content(
                    model=self._settings.embedding_model,
                    contents=batch,
                    config=EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=self._settings.embedding_dimensions,
                    ),
                )

            response = _retry(_do, label="embed_documents batch")
            vectors.extend(item.values for item in response.embeddings)

        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding count mismatch: sent {len(texts)}, got {len(vectors)}"
            )
        return vectors

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """Embed several query strings in a single round-trip.

        Query expansion turns one question into 2-3 variants. Embedding them
        one at a time cost one network wait per variant, and the measurement
        on this project was brutal: 3 sequential calls meant ~920 ms of pure
        waiting before retrieval had even started. `embed_content` already
        takes a list, so the same wall-clock buys every variant — the extra
        variants become free rather than 2x slower.
        """
        if not queries:
            return []

        contents = [query[:MAX_EMBED_CHARS] for query in queries]

        def _do() -> Any:
            return self._client.models.embed_content(
                model=self._settings.embedding_model,
                contents=contents,
                config=EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=self._settings.embedding_dimensions,
                ),
            )

        response = _retry(_do, label="embed_queries")
        vectors = [item.values for item in response.embeddings]
        if len(vectors) != len(queries):
            raise RuntimeError(
                f"Embedding count mismatch: sent {len(queries)}, got {len(vectors)}"
            )
        return vectors

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query with the query-side task type."""
        return self.embed_queries([query])[0]


    def _build_contents(
        self,
        question: str,
        context: str,
        previous_interaction_id: str | None,
    ) -> list[dict[str, Any]]:
        """Build the contents array: optional prior turns + the new question.

        Conversation memory is process-local: we keep the last
        `_MAX_HISTORY_TURNS` (q, a) pairs per conversation id and ship them
        back to the model. Retrieval context + system prompt are re-applied
        on every call.
        """
        contents: list[dict[str, Any]] = []
        if previous_interaction_id:
            with _HISTORY_LOCK:
                past_turns = list(
                _CONVERSATION_HISTORY.get(_history_key(previous_interaction_id), [])
            )
            for turn in past_turns[-_MAX_HISTORY_TURNS:]:
                contents.append({"role": turn["role"], "parts": [{"text": turn["text"]}]})
        contents.append({"role": "user", "parts": [{"text": question}]})
        return contents

    def _remember_turn(
        self,
        previous_interaction_id: str | None,
        new_interaction_id: str,
        question: str,
        answer_text: str,
    ) -> None:
        """Append this turn to the history bucket of the *new* id."""
        with _HISTORY_LOCK:
            bucket = _CONVERSATION_HISTORY.setdefault(new_interaction_id, [])
            bucket.append({"role": "user", "text": question})
            bucket.append({"role": "model", "text": answer_text})
            # Trim to keep memory bounded.
            if len(bucket) > _MAX_HISTORY_TURNS * 2:
                del bucket[: len(bucket) - _MAX_HISTORY_TURNS * 2]

    def answer_stream(
        self,
        *,
        question: str,
        context: str,
        previous_interaction_id: str | None = None,
        on_interaction_id: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        """Yield answer text incrementally as the model produces it.

        Uses `client.models.generate_content_stream`, which returns chunks
        with a `.text` attribute. Each non-empty text is yielded to the
        caller. The full text is also buffered locally so we can register
        the turn against the conversation history once the stream ends.

        `cancel_event` lets the caller stop generation early (e.g. when
        the user clicks *Stop* in the UI). When set, the generator exits
        as soon as the next SDK iteration completes, instead of streaming
        to natural completion.

        **Never silently drops the answer.** If the SDK raises before any
        text is yielded, the exception is wrapped as a `RateLimitError` /
        `TransientError` / `RuntimeError` so the API layer can surface it
        to the user — an empty assistant bubble must never reach the
        client because of a swallowed exception.
        """
        contents = self._build_contents(question, context, previous_interaction_id)
        config = {
            "system_instruction": _system_prompt(context),
            "temperature": 0.2,
        }

        new_interaction_id = f"local-{id(contents)}"
        models = _generation_models()

        for position, model in enumerate(models):
            is_last = position == len(models) - 1
            buffered: list[str] = []

            try:
                stream = self._client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                for chunk in stream:
                    if cancel_event is not None and cancel_event.is_set():
                        log.info("Stream cancelled by client")
                        break
                    text = getattr(chunk, "text", None)
                    if not text:
                        continue
                    buffered.append(text)
                    yield text
            except Exception as exc:
                if not buffered and not is_last and _should_failover(exc):
                    log.warning(
                        "Chat model %s unavailable (%s); failing over to %s",
                        model,
                        exc,
                        models[position + 1],
                    )
                    continue
                raise _generation_error(exc) from exc
            else:
                if buffered:
                    self._remember_turn(
                        previous_interaction_id,
                        new_interaction_id,
                        question,
                        "".join(buffered),
                    )
                if on_interaction_id is not None:
                    on_interaction_id(new_interaction_id)
                return

        raise TransientError("No chat model was available for this request.")

    def answer(
        self,
        *,
        question: str,
        context: str,
        previous_interaction_id: str | None = None,
    ) -> tuple[str, str | None]:
        """Return `(text, interaction_id)` for a non-streaming call."""
        contents = self._build_contents(question, context, previous_interaction_id)

        # Two attempts, not four: a saturated model answers 503 immediately,
        # so a long backoff is time the user spends waiting for nothing. After
        # the second attempt we switch models rather than keep knocking.
        models = _generation_models()
        response = None
        last_exc: BaseException | None = None

        for position, model in enumerate(models):
            is_last = position == len(models) - 1

            def _do(model: str = model) -> Any:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={
                        "system_instruction": _system_prompt(context),
                        "temperature": 0.2,
                    },
                )

            try:
                response = _retry(_do, label=f"answer[{model}]", attempts=2)
                break
            except Exception as exc:
                last_exc = exc
                if is_last or not _should_failover(exc):
                    raise _generation_error(exc) from exc
                log.warning(
                    "Chat model %s unavailable (%s); failing over to %s",
                    model, exc, models[position + 1],
                )

        if response is None:
            raise TransientError(
                f"No chat model was available for this request: {last_exc}"
            )

        text = getattr(response, "text", None) or ""
        interaction_id = f"local-{id(response)}"
        if text:
            self._remember_turn(previous_interaction_id, interaction_id, question, text)
        return text, interaction_id


def _friendly_error(exc: BaseException, fallback: str) -> str:
    """Reduce an SDK exception to a one-sentence, user-readable message."""
    code = getattr(exc, "code", None)
    raw = str(exc) or repr(exc)
    if code == 429 or "RESOURCE_EXHAUSTED" in raw or "quota" in raw.lower():
        return (
            "Gemini rate-limit / quota exceeded. The free tier allows ~20 "
            "requests/day; wait or upgrade your plan, then try again."
        )
    if code in (401, 403) or "API key" in raw or "PERMISSION_DENIED" in raw:
        return "Gemini rejected the API key. Check GEMINI_API_KEY in backend/.env."
    if code in (404,) or "not found" in raw.lower():
        return (
            "Gemini does not recognise the configured model. Verify "
            "CHAT_MODEL in backend/.env (e.g. 'gemini-2.5-flash')."
        )
    if "timeout" in raw.lower() or "timed out" in raw.lower():
        return "Gemini timed out before responding. Please try again."
    return f"Gemini {fallback} error: {raw[:300]}"


def _system_prompt(context: str) -> str:
    """Grounding instructions. The retrieval layer has already decided whether
    the context is trustworthy, so this focuses on *how* to use it."""
    return f"""You are a precise research assistant answering questions about the user's documents.

You have been given numbered excerpts retrieved from those documents. Each excerpt is tagged with its document name and a locator (page, slide, sheet, block or image), so you can point at the exact place a claim came from rather than just the file.

<excerpts>
{context}
</excerpts>

Rules:
- Answer only from the excerpts above. They are the entire body of knowledge you have.
- Cite every factual claim inline using its bracketed tag, e.g. [1] or [2, 3]. Place the tag right after the sentence or clause it supports.
- If the excerpts do not contain the answer, say so plainly: state that the documents do not cover it, and suggest what the user could ask instead. Do not guess, do not fill gaps from general knowledge, and do not speculate.
- If the excerpts conflict with each other, say so and present both positions.
- Be direct and concise. Use a short paragraph or a tight bullet list. Skip preamble like "Based on the provided context".
- If the question is ambiguous, give the most likely useful answer and note the ambiguity in one line."""


_provider: GeminiProvider | None = None


def get_provider() -> GeminiProvider:
    """Return the process-wide provider, constructing it on first use."""
    global _provider
    if _provider is None:
        _provider = GeminiProvider()
    return _provider