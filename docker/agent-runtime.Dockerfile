FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    AGENT_ARTIFACT_DIR=/app/artifact

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/artifact \
    && chown -R appuser:appuser /app/artifact
USER appuser

EXPOSE 8000
CMD ["uvicorn", "heart_of_the_swarm.runtime.api:app", "--host", "0.0.0.0", "--port", "8000"]
