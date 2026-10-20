# --- Build ---
FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir pip-tools

COPY requirements.txt .
RUN pip-compile requirements.txt -o requirements.lock.txt --quiet

# --- Runtime ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG INSTALL_LIBREOFFICE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && if [ "$INSTALL_LIBREOFFICE" = "1" ]; then \
         apt-get install -y --no-install-recommends \
           libreoffice-writer libreoffice-calc libreoffice-impress \
           fonts-dejavu-core fonts-liberation2 \
         && fc-cache -f; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /build/requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY app ./app

# The vector store and uploads live on a volume so they survive redeploys.
ENV DATA_DIR=/data
RUN mkdir -p /data/uploads /data/chroma /data/converted
VOLUME ["/data"]

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
