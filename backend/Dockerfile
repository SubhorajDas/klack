# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f

FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

FROM base AS development

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uvicorn", "klack.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--no-access-log"]

FROM base AS builder

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN groupadd --gid 10001 klack \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin klack

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=klack:klack alembic.ini ./
COPY --chown=klack:klack alembic ./alembic

USER klack
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

CMD ["uvicorn", "klack.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
