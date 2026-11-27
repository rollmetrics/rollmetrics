# syntax=docker/dockerfile:1
FROM node:24-slim AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN uv pip install --system --no-cache -e .

COPY knowledge_base/ ./knowledge_base/
COPY .agents/ ./.agents/
COPY DESIGN_LANGUAGE.md ./
COPY brand/ ./brand/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/data/runtime /app/data/uploads /app/data/renders

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn rollmetrics.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
