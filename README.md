# test-proj-mccbasis

Async payment processing microservice: a FastAPI API and a FastStream (RabbitMQ) consumer sharing PostgreSQL, using the outbox pattern. This is a walking skeleton; the full README comes with ticket 07.

## Run

```
docker compose up --build
```

- API on http://localhost:8000 (`GET /health`; protected routes use `X-API-Key: dev-api-key`)
- RabbitMQ management UI on http://localhost:15672 (guest / guest)

## Check

```
scripts/check
```

Runs ruff, ruff format --check, pyright and pytest. Integration tests need Docker (testcontainers); without it they are skipped with a reason.
