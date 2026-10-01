# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    MYPY_CACHE_DIR=/tmp/.mypy_cache \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    PYTEST_ADDOPTS="-p no:cacheprovider"

RUN useradd --create-home --uid 1000 app

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY seeds ./seeds
COPY app ./app
COPY tests ./tests

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
