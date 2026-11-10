# 01: Walking skeleton

**What to build:** A runnable, checkable empty service. A uv project (Python 3.12) with the `payments` package and settings from environment variables, an API exposing an unauthenticated `/health`, and a consumer process that starts and idles. One Dockerfile serves both. Compose runs postgres and rabbitmq (with healthchecks), a one-shot `migrate` service applying an empty Alembic baseline, then `api` and `consumer` once migrations have succeeded and both backing services are healthy. A pytest harness starts Postgres and RabbitMQ via testcontainers, and skips with an explicit reason when Docker is unavailable. `scripts/check` runs ruff check, ruff format --check, pyright (basic) and pytest.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `docker compose up` brings up postgres, rabbitmq, migrate (exits 0), api and consumer, and `GET /health` returns 200
- [ ] All settings from the spec exist with their defaults (`DATABASE_URL`, `RABBITMQ_URL`, `API_KEY`, Emulator, retry, Webhook, concurrency and Relay settings)
- [ ] A system test against the testcontainers harness hits `/health` and passes
- [ ] Without Docker, integration tests are skipped with a clear reason, not failed
- [ ] `scripts/check` passes
