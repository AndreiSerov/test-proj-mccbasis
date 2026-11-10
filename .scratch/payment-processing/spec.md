# Spec: Async payment processing service

Status: ready-for-agent

Source task: `docs/task/task.pdf`. Decisions: `docs/adr/0001`–`0003`. Vocabulary: `GLOSSARY.md`.

## Problem Statement

A merchant client needs to take payments without blocking on a slow external gateway. It wants to submit a Payment, get an immediate acknowledgement, and learn the outcome later through a Webhook. A retried HTTP request must never create a second Payment, and no accepted Payment may be silently lost, whether the API, the broker, the consumer or the client's Webhook endpoint fails along the way. A reviewer of this test task also needs to run the whole thing with one command and watch the Outbox, retries and DLQ work.

## Solution

A microservice with two runtime processes sharing a PostgreSQL database and a RabbitMQ broker:

- **API** (FastAPI): accepts `POST /api/v1/payments` with an Idempotency key and returns 202 at once. In one transaction it writes the Payment and an Outbox event. It also serves `GET /api/v1/payments/{payment_id}`. All `/api/v1/*` routes require a static `X-API-Key`.
- **Consumer** (FastStream): runs the Relay, which publishes Outbox events to RabbitMQ, and one handler for `payments.new`. The handler runs the Emulator (2–5 s, 90% success), commits the final status and sends the Webhook. On a Delivery failure the message goes through three Retry queues with 1 s, 2 s and 4 s delays. After that it goes to the DLQ.

`docker compose up` starts postgres, rabbitmq, a one-shot migrate step, api and consumer. An optional `demo` profile adds a webhook-sink that can be told to fail, so a reviewer can watch retries and the DLQ happen. An English README documents how to run it, curl examples, the architecture and the trade-offs.

## User Stories

1. As a client, I want to create a Payment with amount, currency, description, metadata and webhook_url, so that I can charge a customer.
2. As a client, I want the create call to return 202 with `payment_id`, `status` and `created_at` immediately, so that my request is not blocked by gateway latency.
3. As a client, I want new Payments to start as `pending`, so that I know processing has not finished.
4. As a client, I want to send an `Idempotency-Key` header on every create, so that retrying a timed-out request never charges twice.
5. As a client, I want a repeated create with the same Idempotency key and the same body to return the original Payment, so that retries are transparent.
6. As a client, I want a repeated create with the same Idempotency key but a different body to return 409, so that I notice a key reuse bug.
7. As a client, I want two concurrent creates with the same Idempotency key to produce exactly one Payment, so that racing retries are safe.
8. As a client, I want a create without an `Idempotency-Key` header to be rejected with 422, so that I can't accidentally skip duplicate protection.
9. As a client, I want amounts validated as positive decimals with at most 2 decimal places, so that invalid charges are rejected with 422.
10. As a client, I want amounts returned as strings, so that no precision is lost in JSON.
11. As a client, I want currency limited to RUB, USD and EUR, so that unsupported currencies are rejected with 422.
12. As a client, I want description length validated (1–255), so that bad input is rejected early.
13. As a client, I want to attach arbitrary JSON metadata (default `{}`), so that I can correlate Payments with my own records.
14. As a client, I want webhook_url validated as an HTTP(S) URL, so that I find out at once if it is malformed.
15. As a client, I want to fetch a Payment by id and see all its fields, including status and processed_at, so that I can poll when I miss a Webhook.
16. As a client, I want 404 for an unknown payment id and 422 for a malformed one, so that errors are clear.
17. As a client, I want every `/api/v1/*` call without a valid `X-API-Key` to return 401, so that the service is not open to anyone.
18. As an operator, I want the API key compared in constant time and read from configuration, so that it is neither guessable by timing nor hard-coded.
19. As an operator, I want an unauthenticated `/health` endpoint, so that container healthchecks work.
20. As a client, I want my Payment to move to `succeeded` or `failed` within seconds, so that I can fulfil or cancel the order.
21. As a client, I want a Webhook POSTed to my webhook_url with `payment_id`, `status`, `amount`, `currency`, `processed_at` and `metadata`, so that I learn the outcome without polling.
22. As a client, I want a Decline to be reported by Webhook like a success and not retried, so that a declined card is a normal outcome and not an incident.
23. As a client, I want the Webhook retried when my endpoint returns non-2xx or times out (5 s), so that a short outage on my side doesn't lose the notification.
24. As a client, I want to know Webhooks are delivered at least once, so that I make my receiver idempotent on `payment_id`.
25. As a client, I want a retried message to never re-run the Emulator once the Payment is final, so that the outcome I'm told never flips.
26. As an operator, I want each accepted Payment's Outbox event written in the same transaction as the Payment, so that no Payment exists without its event.
27. As an operator, I want the Relay to publish unpublished Outbox events in order, with publisher confirms, and mark them `published_at`, so that the broker gets every event even after a crash.
28. As an operator, I want the Relay to claim rows with `FOR UPDATE SKIP LOCKED`, so that running more than one consumer is safe.
29. As an operator, I want published Outbox events kept rather than deleted, so that I have an audit trail.
30. As an operator, I want a handler that receives a duplicate message (the Relay delivers at least once) to handle it idempotently, so that duplicates are harmless.
31. As an operator, I want Delivery failures retried after 1 s, 2 s and 4 s through broker Retry queues, so that waiting messages don't tie up the consumer and survive restarts.
32. As an operator, I want a message that still fails after 3 retries to land in `payments.new.dlq` with the last error in a header, so that I can inspect it and re-drive it by hand.
33. As an operator, I want the exchanges and queues (`payments`, `payments.dlx`, `payments.new`, the Retry queues, the DLQ) declared automatically and durably, so that a fresh broker works without manual setup.
34. As an operator, I want the consumer to handle up to `CONSUMER_CONCURRENCY` messages at once (default 10) in a single process, so that throughput is not 1 payment per 3.5 s.
35. As an operator, I want SIGTERM to stop the Relay and let in-flight messages finish, so that deploys don't drop work.
36. As an operator, I want JSON logs with `payment_id` on every record, so that I can trace one Payment across API and consumer.
37. As an operator, I want the Emulator's delay and success rate, the retry delays and concurrency configurable through environment variables, so that tests and demos can run fast and deterministic.
38. As a reviewer, I want `docker compose up` to start postgres, rabbitmq, api and consumer with healthchecks and migrations applied first, so that the service works with one command.
39. As a reviewer, I want an optional `demo` profile with a webhook-sink that logs requests and fails on demand, so that I can watch retries and the DLQ.
40. As a reviewer, I want an English README with the architecture diagram, run steps, curl examples (create, idempotent repeat, get, webhook, forced DLQ), design decisions and known trade-offs, so that I can evaluate the work quickly.
41. As a developer, I want Alembic migrations for `payments` and `outbox`, so that the schema is versioned.
42. As a developer, I want `scripts/check` to run lint, format, type-check and tests, with integration tests skipped clearly when Docker is unavailable, so that I know when work is done.

## Implementation Decisions

**Packaging and layout**
- One uv project on Python 3.12 with a single `payments` package split into `core` (settings, logging), `db` (models, session, repositories), `domain` (Emulator, Request hash), `api` (app, routers, schemas, auth dependency) and `consumer` (FastStream app, handler, Relay, topology, Webhook client).
- One Dockerfile. The api runs uvicorn on the API app, and the consumer runs `faststream run` on the consumer app.
- Settings come from pydantic-settings: `DATABASE_URL`, `RABBITMQ_URL`, `API_KEY`, `EMULATOR_MIN_DELAY` (2), `EMULATOR_MAX_DELAY` (5), `EMULATOR_SUCCESS_RATE` (0.9), `RETRY_DELAYS` (`1,2,4`), `WEBHOOK_TIMEOUT` (5), `CONSUMER_CONCURRENCY` (10), `RELAY_BATCH_SIZE` (100), `RELAY_POLL_INTERVAL` (0.5).

**Schema**
- `payments` has these columns:
  - `id` UUID (generated in the app) as the primary key;
  - `amount` NUMERIC(18,2);
  - `currency` enum (RUB, USD, EUR);
  - `description` varchar(255);
  - `metadata` JSONB, not null, default `{}`;
  - `status` enum (pending, succeeded, failed);
  - `idempotency_key`, unique and not null;
  - `request_hash`, not null;
  - `webhook_url`;
  - `created_at` and `processed_at` (nullable), both timestamptz in UTC.
- `outbox` has `id` bigserial, `aggregate_id` (payment id), `event_type` (`payments.new`), `payload` JSONB, `created_at` and `published_at` (nullable), plus a partial index on unpublished rows.

**API contract**
- `POST /api/v1/payments` takes the headers `X-API-Key` and `Idempotency-Key` (required, max 255) and the body `{amount, currency, description, metadata?, webhook_url}`. It returns 202 `{payment_id, status, created_at}`.
  - If the key exists with the same Request hash, it returns 202 with the original Payment's body.
  - If the key exists with a different Request hash, it returns 409.
  - On a race it catches the unique-constraint violation, re-reads the row and applies the same rule.
- `GET /api/v1/payments/{payment_id}` returns all Payment fields except `request_hash`. Unknown ids return 404, and non-UUID ids return 422.
- `amount` is serialized as a string.
- A missing or wrong `X-API-Key` returns 401 on all `/api/v1/*` routes, with the key compared in constant time. `/health` is open.
- **Request hash:** a SHA-256 of a canonical JSON of the validated body. Keys are sorted, the amount is normalized to 2 decimal places, and the URL is in canonical string form, so semantically equal bodies hash equally.

**Messaging topology** (ADR-0002)
- `payments` is a direct exchange bound to the durable queue `payments.new` with routing key `payments.new`.
- The Retry queues `payments.new.retry.1s`, `.2s` and `.4s` each have a message TTL and dead-letter back to `payments` with routing key `payments.new`. Their TTLs are built from `RETRY_DELAYS`.
- `payments.dlx` is a direct exchange that routes to `payments.new.dlq`.
- The message body is the Outbox payload (at least `payment_id`). The attempt number travels in a header.

**Relay** (ADR-0001)
- The Relay is an asyncio task started and stopped with the FastStream app lifecycle.
- It loops as follows: open a transaction, select up to the batch size of unpublished rows ordered by id with `FOR UPDATE SKIP LOCKED`, publish each with publisher confirms, set `published_at`, then commit. It sleeps for the poll interval when the batch is empty.
- A crash between publish and commit re-publishes the event, which the handler's idempotency absorbs.

**Handler** (ADR-0002, ADR-0003)
1. Lock the Payment with `SELECT … FOR UPDATE`.
2. If the status is `pending`: run the Emulator, set `succeeded` or `failed` (a Decline) and `processed_at`, then commit.
3. If the Payment is already final: skip the Emulator.
4. Send the Webhook (httpx, timeout `WEBHOOK_TIMEOUT`, 2xx means success) after the commit.
5. On a Delivery failure (Webhook non-2xx or timeout, DB error), publish the message to the next Retry queue with the attempt header incremented, then ack. Once the attempts are exhausted, publish to the DLQ with the original body and the last error in a header, then ack.
6. An unknown `payment_id` goes straight to the DLQ.

- **Emulator:** awaits a random delay in `[min, max]` and returns succeeded with probability `success_rate`. Its random source and sleep are injectable.
- **Webhook payload:** `{payment_id, status, amount, currency, processed_at, metadata}`. It is not signed.

**Compose**
- The services are postgres and rabbitmq (management image), each with a healthcheck, plus a one-shot `migrate` service (`alembic upgrade head`), `api` and `consumer`.
- `api` and `consumer` wait for `migrate` to complete successfully and for postgres and rabbitmq to be healthy.
- `webhook-sink` runs under `profiles: [demo]`. It is a tiny FastAPI app that logs every request and returns 500 when the URL has `?fail=1`.

**Observability:** JSON logging through stdlib `logging`, with a `payment_id` field on every record about a Payment.

## Testing Decisions

- Good tests here check behaviour a client or operator can see: HTTP responses, Webhook calls received, Payment status read back, and messages sitting in the DLQ. They never assert on internal functions, repositories or SQL, so the inside can be refactored freely.
- **Seam 1: the whole system, black box (main).** pytest with Postgres and RabbitMQ from testcontainers and migrations applied. An httpx client calls the FastAPI app, and the consumer app (Relay and handler) runs in the same event loop. A stub HTTP Webhook receiver records calls and can be scripted to return errors or delay responses. Behaviour is controlled only through settings: success rate 1.0 or 0.0, zero Emulator delay, short retry delays. The scenarios are:
  - Auth: 401 without a key or with a wrong one, and an open `/health`.
  - Validation: each 422 case, a missing Idempotency key, and amount precision.
  - Create, then the Webhook arrives with `succeeded`, then `GET` shows `succeeded` with `processed_at`.
  - A Decline: Webhook `failed`, exactly one Webhook call, nothing in the DLQ.
  - The same key and body return the same `payment_id`, with one Payment and one Outbox event. The same key with a different body returns 409.
  - Concurrent duplicate creates produce exactly one Payment.
  - The receiver fails twice and then succeeds: the Webhook is delivered, the Emulator ran once, and the status never changes.
  - The receiver always fails: after 4 calls in total the message is in `payments.new.dlq` with an error header.
  - The Outbox event's `published_at` gets set after a create.
  - Publishing the same event twice by hand results in one Emulator run and the same final status.
- **Seam 2: pure-function unit tests (small).**
  - The Request hash is stable across metadata key order and `10.0`/`10.00`, and differs for different bodies.
  - The Emulator with a seeded `random.Random` and a fake sleep respects the success rate and delay bounds.
- Integration tests skip with an explicit reason when Docker is unavailable. `scripts/check` runs ruff check, ruff format --check, pyright (basic) and pytest.
- **Prior art:** none yet, since this is a greenfield repo. These tests set the pattern.

## Out of Scope

- An automatic DLQ re-drive or sweeper for Payments stuck in `pending`. Re-drive is documented as a manual step.
- HMAC signing of Webhooks. It's mentioned in the README as an extension.
- Metrics, tracing and structlog.
- Real payment gateways, refunds, cancellation, listing or searching Payments.
- Per-client API keys, rate limiting and multi-tenancy.
- A separate Relay service and the delayed-message exchange plugin (see ADRs).

## Further Notes

- Delivery to the broker and of Webhooks is at least once. Duplicates are absorbed by the handler's row lock and status check, and receivers should dedupe on `payment_id`. The README states this.
- A Payment is "processed" (`processed_at` set) once the Emulator result is committed, independently of whether its Webhook has been delivered yet.
- The `migrate` service is a short-lived extra next to the four required runtime services, and `webhook-sink` exists only under the `demo` profile.
