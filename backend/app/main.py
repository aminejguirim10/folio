from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.feedback import router as feedback_router
from app.config import get_settings
from app.core.security import enforce_api_key
from app.models.schemas import HealthResponse
from app.services.catalog_service import get_catalog
from app.services.vector_service import get_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    try:
        index = get_index()
        catalog = get_catalog()
        known = set(index.doc_ids())
        catalog.resurrect(known)
        catalog.reconcile(known)
        log.info(
            "Startup: %d documents, %d chunks",
            index.document_count(),
            index.chunk_count(),
        )
    except Exception as exc: 
        log.warning("Index warm-up failed; API will report degraded: %s", exc)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    settings.ensure_dirs()

    app = FastAPI(
        title="Folio",
        description=(
            "Grounded question answering over uploaded documents — PDF, "
            "Office, CSV, Markdown, HTML, JSON and images transcribed by "
            "Gemini. Gemini for generation and embeddings, hybrid "
            "dense+BM25 retrieval."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        max_age=600,
    )

    auth_dependency = [Depends(enforce_api_key)]

    documents_router.dependencies = auth_dependency
    chat_router.dependencies = auth_dependency
    feedback_router.dependencies = auth_dependency

    app.include_router(documents_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Liveness plus a real readiness signal.

        Reports provider status instead of crashing at import when the API key
        is missing, so a misconfigured deploy is diagnosable.
        """
        try:
            get_index()
            index = get_index()
            documents, chunks = index.document_count(), index.chunk_count()
        except Exception as exc: 
            log.warning("Index unavailable: %s", exc)
            documents, chunks = 0, 0

        provider_ok = bool(settings.gemini_api_key)
        return HealthResponse(
            status="ok" if provider_ok else "degraded",
            provider_configured=provider_ok,
            documents=documents,
            chunks=chunks,
            model=settings.chat_model,
            details={} if provider_ok else {"error": "GEMINI_API_KEY is not set"},
        )

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "Folio API", "docs": "/docs"}

    return app


app = create_app()
