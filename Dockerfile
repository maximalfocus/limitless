# syntax=docker/dockerfile:1

# Everything the project needs — Python, its dependencies, both services, the demonstration runner,
# the tests, and the linters — lives inside these images. The host needs Docker and nothing else:
# no PostgreSQL, no Python environment, no tuning of file-descriptor or connection limits.

FROM python:3.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

RUN groupadd --gid 10001 limitless \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin limitless

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./


FROM base AS runtime-deps
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev


FROM runtime-deps AS app
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable
# The expansion fixture is generated here, at build time, by the checked-in generator. It is never
# committed: the repository carries the recipe, and the recipe is unremarkable.
RUN python -m limitless.generate_expansion_fixture --output /fixtures/expansion.ndjson.gz \
 && chmod -R a+rX /fixtures
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "limitless.secure.app:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS verify
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    MYPY_CACHE_DIR=/tmp/mypy-cache
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY src ./src
COPY tests ./tests
COPY docker-compose.yml ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-editable
# The suite exercises the expansion fixture, so this stage builds it the same way the app stage does.
RUN python -m limitless.generate_expansion_fixture --output /fixtures/expansion.ndjson.gz \
 && chmod -R a+rX /fixtures
USER 10001:10001
CMD ["pytest", "-p", "no:cacheprovider"]
