from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.services.gemini_provider import Citation, get_provider
from app.services.vector_service import get_index, tokenize

log = logging.getLogger(__name__)

# Rolling buffer of recent retrievals. Bounded so we don't leak memory.
_RECAP_BUFFER: deque[dict[str, Any]] = deque(maxlen=200)
_RECAP_LOCK = __import__("threading").Lock()

RRF_K = 60  # standard Reciprocal Rank Fusion damping constant
MMR_LAMBDA = 0.72  # weight on relevance vs. diversity (higher = more relevant)

SPARSE_WEIGHT = 0.0
_QUERY_VECTOR_CACHE: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_QUERY_VECTOR_CACHE_MAX = 256
_QUERY_VECTOR_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class RetrievalResult:
    citations: list[Citation]
    grounded: bool
    context: str

    @property
    def top_score(self) -> float:
        return self.citations[0].score if self.citations else 0.0


def _token_set(text: str) -> set[str]:
    return set(text.lower().split())


def _mmr(
    candidates: list[dict[str, Any]], *, top_k: int, lambda_: float
) -> list[dict[str, Any]]:
    """Greedy Maximal Marginal Relevance selection."""
    if not candidates:
        return []
    
    pool: list[tuple[dict[str, Any], set[str]]] = [
        (item, _token_set(item["text"])) for item in candidates
    ]
    selected: list[dict[str, Any]] = []
    selected_tokens: list[set[str]] = []

    while pool and len(selected) < top_k:
        best_score = -1.0
        best_index = -1

        for position, (item, tokens) in enumerate(pool):
            redundancy = max(
                (len(tokens & chosen) / max(1, len(tokens)) for chosen in selected_tokens),
                default=0.0,
            )
            mmr_score = lambda_ * item["score"] - (1 - lambda_) * redundancy
            if mmr_score > best_score:
                best_score, best_index = mmr_score, position

        if best_index < 0:
            break
        item, tokens = pool.pop(best_index)
        selected.append(item)
        selected_tokens.append(tokens)

    return selected


_MULTI_QUERY_VARIANTS = 3  


def _expand_query(question: str) -> list[str]:
    """Return cheap heuristic rewrites of the question.

    We deliberately avoid calling the LLM for expansion: a single
    round-trip to Gemini doubles retrieval latency, and at the eval
    corpus size the gain was within noise. These string-level transforms
    surface the same BM25 hits that an LLM would, at a fraction of the
    cost.
    """
    out: list[str] = [question]

    plain = "".join(ch for ch in question.lower() if ch.isalnum() or ch.isspace())
    plain = " ".join(plain.split())
    if plain and plain != question.lower():
        out.append(plain)

    prefixes = (
        "what is ", "what are ", "what was ", "what were ",
        "how is ", "how does ", "how do ", "how many ", "how much ",
        "when did ", "when is ", "when was ",
        "where is ", "where are ",
        "why is ", "why are ", "why does ", "why do ",
        "who is ", "who are ",
        "explain ", "describe ", "summarise ", "summarize ",
        "tell me about ", "give me ", "find ", "show ",
    )
    lowered = question.lower().lstrip()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            stripped = question[len(prefix):].rstrip("?.")
            if stripped and stripped not in out:
                out.append(stripped)
            break

    return out[:_MULTI_QUERY_VARIANTS]


def _rerank(
    candidates: list[dict[str, Any]],
    question: str,
    *,
    literal_weight: float = 0.35,
) -> list[dict[str, Any]]:
    """Boost candidates that contain tokens from the literal question.

    Score = absolute_score * (1 + literal_weight * token_overlap_ratio).
    Token overlap ratio is the share of query tokens present in the passage.
    We use set overlap so repeated words in the passage don't dominate.
    """
    q_tokens = set(tokenize(question))
    if not q_tokens:
        return candidates
    reranked: list[dict[str, Any]] = []
    for item in candidates:
        text_tokens = set(tokenize(item["text"]))
        overlap = len(q_tokens & text_tokens) / len(q_tokens)
        # `score` is the absolute signal from fusion (cosine or normalized BM25).
        new_score = float(item["score"]) * (1.0 + literal_weight * overlap)
        item = {**item, "score": new_score, "literal_overlap": round(overlap, 3)}
        reranked.append(item)
    return sorted(reranked, key=lambda x: x["score"], reverse=True)


def _fuse(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    *,
    sparse_weight: float = SPARSE_WEIGHT,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion over both result lists.

    `sparse_weight` matters more than it looks. Sum-fusion rewards items both
    retrievers found, which *demotes* a passage the dense retriever ranked
    first but BM25 missed. Measured on the eval corpus, a weight of 0.7 cost
    ~0.08 MRR against dense alone. The lexical arm is a rescue path for exact
    identifiers, not a co-equal voter, so it is weighted low.

    Each fused entry carries three numbers:
    * `rrf` — rank-fusion score, used only for *ordering* within this result list.
    * `dense_score` — cosine similarity in [0, 1] from the dense retriever. The
      absolute signal the relevance gate uses.
    * `bm25` — raw BM25 score from the sparse retriever. Gate signal too.

    The `score` we surface to the caller is the **maximum** of the two
    absolute signals, not the relative RRF — so the UI can show real cosine
    similarity instead of "the top hit always looks like 100%".
    """
    fused: dict[tuple[str, int, str], dict[str, Any]] = {}

    for results, weight in ((dense, 1.0), (sparse, sparse_weight)):
        for rank, item in enumerate(results):
            key = (item["doc_id"], item["page"], item["text"][:120])
            if key in fused:
                entry = fused[key]
                entry["rrf"] += weight / (RRF_K + rank + 1)
                if item.get("dense_score") is not None:
                    entry["dense_score"] = max(
                        entry.get("dense_score") or 0.0, item["dense_score"]
                    )
                if item.get("bm25") is not None:
                    entry["bm25"] = max(entry.get("bm25") or 0.0, item["bm25"])
            else:
                fused[key] = {
                    "doc_id": item["doc_id"],
                    "source": item["source"],
                    "page": item["page"],
                    "text": item["text"],
                    "locator": item.get("locator", ""),
                    "rrf": weight / (RRF_K + rank + 1),
                    "dense_score": item.get("dense_score"),
                    "bm25": item.get("bm25"),
                }

    ranked = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
    if not ranked:
        return []

    for item in ranked:
        dense = float(item.get("dense_score") or 0.0)
        bm25 = float(item.get("bm25") or 0.0)
        item["score"] = max(dense, _normalize_bm25(bm25))
        item["rrf_score"] = item["rrf"]
    return ranked


_BM25_SATURATION = 12.0 


def _normalize_bm25(score: float) -> float:
    if score <= 0:
        return 0.0
    return min(1.0, score / _BM25_SATURATION)


def _is_grounded(
    candidates: list[dict[str, Any]], *, dense_floor: float, lexical_floor: float
) -> bool:
    """Decide whether the retrieved evidence is strong enough to answer from.

    Gating on the fused rank score cannot work: that score is normalised
    against the best hit in its own result set, so the top passage is always
    1.0 no matter how badly it matched. An out-of-scope query would clear any
    threshold. So the floor is applied to *absolute* evidence instead:

    * `dense_floor` on cosine similarity, which is calibrated across queries for
      a given embedding model, or
    * `lexical_floor` on BM25, for the exact-identifier queries where dense
      retrieval is known to underperform and BM25 legitimately finds a hit the
      embedder missed.
    """
    best_dense = max(
        (c.get("dense_score") or 0.0 for c in candidates), default=0.0
    )
    best_lexical = max((c.get("bm25") or 0.0 for c in candidates), default=0.0)
    return best_dense >= dense_floor or best_lexical >= lexical_floor


def _embed_variants(variants: list[str]) -> list[list[float]]:
    """Embed every query variant in one round-trip, reusing cached vectors.

    Two independent wins stack here:

    * **Batching.** Expansion turns one question into 2-3 variants. Embedding
      them one at a time meant 2-3 sequential network waits before retrieval
      could even start — measured at 923 ms of a 955 ms `retrieve()` call,
      i.e. 97% of the latency was waiting on the network, not searching.
      One `embed_content` call with a list collapses that back to a single
      wait, and the extra variants become free.
    * **Caching.** A chat UI re-sends the same wording constantly (a retry, a
      re-ask, the same question twice in a session). An embedding is
      deterministic for a given (model, text), so those repeats cost nothing.

    Returns an empty vector for any variant that could not be embedded: the
    caller already treats that as "no dense arm for this variant" and falls
    back to BM25, which still answers exact-identifier questions.
    """
    settings = get_settings()
    model = settings.embedding_model

    with _QUERY_VECTOR_CACHE_LOCK:
        missing = [v for v in variants if (model, v) not in _QUERY_VECTOR_CACHE]

    if missing:
        try:
            fresh = get_provider().embed_queries(missing)
        except Exception as exc:
            log.warning("Query embedding failed, falling back to BM25 only: %s", exc)
            fresh = []
        if len(fresh) != len(missing):
            # Misalignment would pair a variant with the wrong search, so only
            # accept a response that came back complete and in order.
            log.warning(
                "Query embedding incomplete: sent %d, got %d", len(missing), len(fresh)
            )
            fresh = []
        if fresh:
            with _QUERY_VECTOR_CACHE_LOCK:
                for variant, vector in zip(missing, fresh, strict=True):
                    _QUERY_VECTOR_CACHE[(model, variant)] = vector
                while len(_QUERY_VECTOR_CACHE) > _QUERY_VECTOR_CACHE_MAX:
                    _QUERY_VECTOR_CACHE.popitem(last=False)

    with _QUERY_VECTOR_CACHE_LOCK:
        return [_QUERY_VECTOR_CACHE.get((model, v), []) for v in variants]


def retrieve(
    question: str,
    *,
    doc_ids: list[str] | None = None,
    top_k: int | None = None,
    min_relevance: float | None = None,
) -> RetrievalResult:
    """Run the full retrieval pipeline for one question.

    Side effect: every call records its outcome (top score, gate decision,
    latency) into a bounded ring buffer so an operator can see at a glance
    whether the corpus is empty, the gate is too strict, or embeddings
    are returning junk.
    """
    settings = get_settings()
    top_k = top_k or settings.top_k
    floor = settings.min_relevance if min_relevance is None else min_relevance
    index = get_index()

    t0 = time.perf_counter()
    if index.empty():
        _record("empty_corpus", question, 0.0, False, 0.0, 0, t0, doc_ids)
        return RetrievalResult(citations=[], grounded=False, context="")

    variants = _expand_query(question)
    vectors = _embed_variants(variants)

    dense_all: list[dict[str, Any]] = []
    sparse_all: list[dict[str, Any]] = []
    for variant, qv in zip(variants, vectors, strict=True):
        if qv:
            dense_all.extend(
                index.dense_search(qv, top_k=settings.candidate_k, doc_ids=doc_ids)
            )
        sparse_all.extend(
            index.sparse_search(variant, top_k=settings.candidate_k, doc_ids=doc_ids)
        )

    def _dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, int, str]] = set()
        out: list[dict[str, Any]] = []
        for it in items:
            key = (it["doc_id"], it["page"], it["text"][:120])
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    dense = _dedup(dense_all)
    sparse = _dedup(sparse_all)

    fused = _fuse(dense, sparse)
    if not fused:
        _record("no_candidates", question, 0.0, False, 0.0, 0, t0, doc_ids)
        return RetrievalResult(citations=[], grounded=False, context="")

    fused = _rerank(fused, question)

    # Gate on absolute evidence, before any rank-relative normalisation.
    best_dense = max((c.get("dense_score") or 0.0 for c in fused), default=0.0)
    best_lex = max((c.get("bm25") or 0.0 for c in fused), default=0.0)
    if not _is_grounded(
        fused,
        dense_floor=floor,
        lexical_floor=settings.min_lexical_score,
    ):
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _record(
            "below_floor", question, max(best_dense, _normalize_bm25(best_lex)),
            False, best_dense, len(fused), t0, doc_ids,
        )
        log.info(
            "retrieval below_floor dense=%.3f bm25=%.2f candidates=%d elapsed_ms=%.1f q=%r",
            best_dense, best_lex, len(fused), elapsed_ms, question[:80],
        )
        return RetrievalResult(citations=[], grounded=False, context="")

    selected = (
        _mmr(fused, top_k=top_k, lambda_=MMR_LAMBDA)
        if settings.enable_mmr
        else fused[:top_k]
    )

    citations: list[Citation] = []
    for item in selected:
        score = float(item["score"])
        citations.append(
            Citation(
                doc_id=item["doc_id"],
                source=item["source"],
                page=int(item["page"]),
                score=score,
                snippet=item["text"].strip()[:600],
                locator=item.get("locator") or f"Page {item['page']}",
            )
        )
        if len(citations) >= top_k:
            break

    if not citations:
        _record("no_citations", question, best_dense, False, best_dense, len(fused), t0, doc_ids)
        return RetrievalResult(citations=[], grounded=False, context="")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _record(
        "grounded", question, citations[0].score, True, best_dense, len(citations), t0, doc_ids,
    )
    log.info(
        "retrieval grounded candidates=%d top_score=%.3f dense=%.3f bm25=%.2f elapsed_ms=%.1f q=%r",
        len(citations), citations[0].score, best_dense, best_lex, elapsed_ms, question[:80],
    )
    return RetrievalResult(
        citations=citations,
        grounded=True,
        context=build_context(citations),
    )


def _record(
    outcome: str,
    question: str,
    top_score: float,
    grounded: bool,
    best_dense: float,
    n_candidates: int,
    started_perf: float,
    doc_ids: list[str] | None,
) -> None:
    """Append a one-line summary to the rolling metrics buffer."""
    record = {
        "ts": time.time(),
        "outcome": outcome,
        "question_len": len(question),
        "top_score": round(top_score, 4),
        "grounded": grounded,
        "best_dense": round(best_dense, 4),
        "n_candidates": n_candidates,
        "elapsed_ms": round((time.perf_counter() - started_perf) * 1000, 1),
        "doc_ids_filter": bool(doc_ids),
    }
    with _RECAP_LOCK:
        _RECAP_BUFFER.append(record)


def recent_stats() -> dict[str, Any]:
    """Snapshot the recent retrievals — counts per outcome, avg latency, etc."""
    with _RECAP_LOCK:
        snap = list(_RECAP_BUFFER)
    if not snap:
        return {"count": 0}
    by_outcome: dict[str, int] = {}
    latencies = []
    grounded = 0
    for r in snap:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        latencies.append(r["elapsed_ms"])
        if r["grounded"]:
            grounded += 1
    return {
        "count": len(snap),
        "grounded": grounded,
        "grounded_ratio": round(grounded / len(snap), 3),
        "outcomes": by_outcome,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 1),
            "max": round(max(latencies), 1),
        },
        "recent": snap[-10:],
    }


def build_context(citations: list[Citation]) -> str:
    """Render citations into the numbered block the model is shown."""
    blocks = []
    for number, citation in enumerate(citations, start=1):
        blocks.append(f"[{number}] {citation.source} — page {citation.page}\n{citation.snippet}")
    return "\n\n".join(blocks)
