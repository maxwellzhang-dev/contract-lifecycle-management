FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATABASE_PATH=/data/contracts.db \
    UPLOAD_DIR=/data/uploads

WORKDIR /app

COPY backend/requirements.txt backend/requirements-rag.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Optional document search (LightRAG): docker build --build-arg INSTALL_RAG=1 …
# The tokenizer file is fetched at build time so the container needs no internet access for it.
ARG INSTALL_RAG=0
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    RAG_DIR=/data/rag
RUN if [ "$INSTALL_RAG" = "1" ]; then \
      pip install --no-cache-dir -r backend/requirements-rag.txt && \
      python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"; \
    fi

COPY backend/app ./backend/app

RUN mkdir -p /data/uploads

EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
