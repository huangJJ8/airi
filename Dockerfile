# AIRI backend image.
#
# Local-demo oriented: SQLite + the deterministic demo_mock LLM substitute +
# synthetic fixtures. No Spark, no MySQL, no API key needed.
#
# NOTE: this image is provided for convenience. Its runtime behaviour has NOT
# been verified in the development environment (no Docker available). See
# docs/guides/release-checklist.md and the Phase 12 implementation report.

FROM python:3.12-slim

# uv, copied from its official image so no curl/pip bootstrap is needed.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency layer: only the manifests, so it caches independently of source.
# `--no-install-project` skips building AIRI itself, which is what lets us sync
# before the source tree exists.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Application layer.
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY scripts ./scripts

# Now install the project itself into the same venv.
RUN uv sync --frozen --no-dev

# Put the project venv first so `alembic` / `uvicorn` resolve without uv.
ENV PATH="/app/.venv/bin:$PATH"

# Demo runtime configuration. `demo_mock` is an explicit substitute, never a
# silent fallback: a broken real provider raises instead of degrading.
ENV AIRI_APP_NAME=AIRI \
    AIRI_ENVIRONMENT=local \
    AIRI_LOG_LEVEL=INFO \
    AIRI_DATABASE_URL=sqlite+pysqlite:///.demo/airi_web_demo.db \
    AIRI_LLM_MODE=demo_mock \
    AIRI_EXECUTION_MODE=mock \
    AIRI_DEMO_FIXTURES=true

EXPOSE 8000

# Migrate, seed synthetic data (idempotent: rebuilds the demo database), serve.
CMD ["sh", "-c", "mkdir -p .demo && alembic upgrade head && python scripts/seed_demo.py && uvicorn airi.main:app --host 0.0.0.0 --port 8000"]
