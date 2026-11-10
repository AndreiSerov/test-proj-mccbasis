# 02: Create and get a Payment

**What to build:** A client can create a Payment and read it back. `POST /api/v1/payments` validates the body (positive amount with at most 2 decimals, currency RUB/USD/EUR, description 1–255, metadata JSON defaulting to `{}`, HTTP(S) webhook_url) and requires an `Idempotency-Key` header. It writes the Payment (`pending`) and its Outbox event (`payments.new`, unpublished) in one transaction and returns 202 `{payment_id, status, created_at}`. `GET /api/v1/payments/{payment_id}` returns every Payment field except the Request hash. `amount` is serialized as a string. Every `/api/v1/*` route requires `X-API-Key`, compared in constant time against `API_KEY`. The Alembic migration creates `payments` and `outbox` as specified, including the unique Idempotency key and the partial index on unpublished Outbox events. Deduplication by Idempotency key is not part of this ticket; a stored but unchecked key is fine.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Create returns 202 with `pending`; GET returns all fields with `amount` as a string and `processed_at` null
- [ ] Each create leaves exactly one Payment and one unpublished Outbox event, written atomically
- [ ] 401 without a key or with a wrong one on both endpoints; `/health` stays open
- [ ] 422 for a missing `Idempotency-Key`, amount ≤ 0, more than 2 decimals, unknown currency, bad description length, malformed webhook_url, non-UUID id
- [ ] 404 for an unknown payment id
- [ ] Migration upgrades and downgrades cleanly
- [ ] System tests through the API cover the above; `scripts/check` passes
