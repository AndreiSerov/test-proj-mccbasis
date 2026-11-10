FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

# One image for all services; compose overrides the command for migrate and consumer.
CMD ["uvicorn", "payments.api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
