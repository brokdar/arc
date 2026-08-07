# Build stage
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies from the lockfile only — cached unless the lock changes
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Runtime stage
FROM python:3.14-alpine
WORKDIR /app

# bash for scripts, curl for the container healthcheck, tzdata because alpine
# ships no zoneinfo database and `app.domain.activity.parse_timezone` resolves
# IANA names through stdlib `zoneinfo` — without it a session whose file named
# a region would fail to date itself in the image and nowhere else.
RUN apk add --no-cache bash curl tzdata

RUN addgroup -g 1001 -S app && \
    adduser -S app -u 1001 -G app

COPY --from=builder --chown=app:app /app /app

USER app

ENV PATH="/app/.venv/bin:$PATH"

CMD ["fastapi", "run", "app/main.py"]
