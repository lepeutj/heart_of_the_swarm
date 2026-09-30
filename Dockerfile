FROM node:22-alpine AS frontend

WORKDIR /build
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/
RUN pnpm --dir frontend install --frozen-lockfile
COPY frontend ./frontend
RUN pnpm --dir frontend build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY --from=frontend /build/src/heart_of_the_swarm/static/workflow-editor \
    ./src/heart_of_the_swarm/static/workflow-editor
RUN uv sync --locked --no-dev --no-editable

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs /app/data \
    && chown -R appuser:appuser /app/logs /app/data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "heart_of_the_swarm.api:app", "--host", "0.0.0.0", "--port", "8000"]
