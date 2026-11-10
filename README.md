# test-proj-mccbasis

Async payment processing microservice: a FastAPI API and a FastStream (RabbitMQ) consumer sharing PostgreSQL, using the outbox pattern. This is a walking skeleton; the full README comes with ticket 07.

## Run

Podman is the default container engine; Docker works the same way.

```
scripts/compose up --build                          # podman compose (docker compose if podman is missing)
CONTAINER_ENGINE=docker scripts/compose up --build  # force Docker
```

`podman compose up --build` or `docker compose up --build` directly works too. With Podman on macOS, start the VM first (`podman machine start`).

- API on http://localhost:8000 (`GET /health`; protected routes use `X-API-Key: dev-api-key`)
- RabbitMQ management UI on http://localhost:15672 (guest / guest)

## API

All `/api/v1/*` routes require `X-API-Key`; `/health` is open. `amount` is a string in responses.

```
curl -X POST localhost:8000/api/v1/payments \
  -H 'X-API-Key: dev-api-key' -H 'Idempotency-Key: order-42' -H 'Content-Type: application/json' \
  -d '{"amount": "10.50", "currency": "USD", "description": "Order 42", "metadata": {"order_id": 42}, "webhook_url": "https://example.com/hook"}'
# 202 {"payment_id": "...", "status": "pending", "created_at": "..."}

curl localhost:8000/api/v1/payments/<payment_id> -H 'X-API-Key: dev-api-key'
```

`Idempotency-Key` makes creates safe to retry. A repeated key with the same body (same Request hash: key order in `metadata` and `10.5` vs `10.50` do not matter) returns 202 with the original Payment's `payment_id`, `created_at` and its current `status`, and creates nothing new. The same key with a different body returns 409. Concurrent creates with one key yield exactly one Payment.

## Check

```
scripts/check
```

Runs ruff, ruff format --check, pyright and pytest. Integration tests start Postgres and RabbitMQ with testcontainers on the same engine choice (`CONTAINER_ENGINE`, default Podman; an explicit `DOCKER_HOST` wins). Without a running engine they are skipped with a reason.

Podman notes:
- On rootless Podman on Linux, testcontainers' Ryuk reaper may fail to start; set `TESTCONTAINERS_RYUK_DISABLED=true`.
- If image pulls fail with `docker-credential-desktop not installed`, remove the leftover `"credsStore": "desktop"` line from `~/.docker/config.json`.
