# syntax=docker/dockerfile:1
# Production image for the Sonic Rocket single-user Streamable HTTP demo.
# Secrets (SMARTSCHOOL_*, MCP_API_KEY) must be injected at runtime — never COPY .env.

FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

# smartschool is pinned to a GitHub rev in uv.lock; git is required to fetch it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1

WORKDIR /app

# Cache third-party deps independently of application source.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev

FROM python:3.12-slim-bookworm

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

USER app

# Console script from pyproject [project.scripts]; listens on /mcp.
CMD ["smartschool-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
