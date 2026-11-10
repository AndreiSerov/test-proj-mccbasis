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

## What happens after create

The API writes the Payment and an Outbox event in one transaction. The consumer's Relay (an asyncio task, ADR-0001) publishes unpublished events to the `payments` exchange and sets `published_at`. The `payments.new` handler locks the Payment, runs the Emulator (2-5 s, 90% success; configurable) if it is still `pending`, commits `succeeded` or `failed` with `processed_at`, then POSTs the Webhook `{payment_id, status, amount, currency, processed_at, metadata}` to `webhook_url` (2xx = delivered). Delivery is at least once, so make your receiver idempotent on `payment_id`.

## Retries and the DLQ

A Delivery failure (Webhook non-2xx or timeout, DB error) is retried through RabbitMQ (ADR-0002): the consumer republishes the message to the Retry queue `payments.new.retry.1s`, then `.2s`, then `.4s` (named from `RETRY_DELAYS`, default `1,2,4`; each queue's TTL dead-letters it back to `payments.new`) and acks the original only after the broker confirms the republish. The attempt number travels in the `x-attempt` header (absent = first attempt). After the third retry fails (4 Webhook calls in total) the message goes to `payments.new.dlq` via `payments.dlx`, with `x-last-error` and `x-attempt` headers. A malformed message or an unknown `payment_id` goes straight to the DLQ. A Decline is a normal outcome and is never retried. A retry of an already-settled Payment skips the Emulator and only re-sends the Webhook. Changing `RETRY_DELAYS` creates new Retry queues and leaves the old ones on the broker; their messages still dead-letter back, so nothing is lost, and they can be deleted once empty. All of this topology is durable and declared by the consumer on start.

To inspect the DLQ: `curl -u guest:guest localhost:15672/api/queues/%2F/payments.new.dlq`, or the management UI. Re-driving a parked message is manual (publish its body again to the `payments` exchange with routing key `payments.new`).

## Concurrency, shutdown and logs

- `CONSUMER_CONCURRENCY` (default 10) is how many messages one consumer process handles at once; it is also the RabbitMQ prefetch, so the broker never hands out more. The consumer's database pool is sized `CONSUMER_CONCURRENCY + 2` (each in-flight handler holds a connection through the Emulator, plus the Relay's).
- On SIGTERM the Relay finishes its current iteration and stops, the consumer stops taking new messages, and in-flight messages finish and are acked before the connection closes (the Relay gets up to 5 s, then in-flight messages up to `GRACEFUL_TIMEOUT`, default 30 s; compose gives the consumer a 45 s grace period, so raise it if you raise the timeout). Anything unacked is redelivered, which is safe because the handler is idempotent.
- API and consumer log one JSON object per line to stderr (`timestamp`, `level`, `logger`, `message`, `exception` when there is one), with a `payment_id` field on records about a Payment. Logging is configured by the app factories, so it applies under `uvicorn` and `faststream run`.

## Check

```
scripts/check
```

Runs ruff, ruff format --check, pyright and pytest. Integration tests start Postgres and RabbitMQ with testcontainers on the same engine choice (`CONTAINER_ENGINE`, default Podman; an explicit `DOCKER_HOST` wins). Without a running engine they are skipped with a reason.

Podman notes:
- On rootless Podman on Linux, testcontainers' Ryuk reaper may fail to start; set `TESTCONTAINERS_RYUK_DISABLED=true`.
- If image pulls fail with `docker-credential-desktop not installed`, remove the leftover `"credsStore": "desktop"` line from `~/.docker/config.json`.
