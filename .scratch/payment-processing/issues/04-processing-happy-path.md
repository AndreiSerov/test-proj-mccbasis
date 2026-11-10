# 04: Processing, happy path

**What to build:** An accepted Payment is processed and its client is notified. The consumer declares the `payments` direct exchange and the durable `payments.new` queue. The Relay, an asyncio task in the consumer lifecycle, claims unpublished Outbox events in id order with `FOR UPDATE SKIP LOCKED` (batch and poll interval from settings), publishes them with publisher confirms, and sets `published_at` (ADR-0001). The handler locks the Payment; if `pending` it runs the Emulator (random delay in `[min, max]`, success with probability `success_rate`, injectable random source and sleep), commits `succeeded` or `failed` with `processed_at`, then POSTs the Webhook `{payment_id, status, amount, currency, processed_at, metadata}` with the configured timeout. If the Payment is already final, the Emulator is skipped and only the Webhook is sent. A Decline is a normal outcome (ADR-0003). Retry queues and the DLQ are not part of this ticket.

**Blocked by:** 02

**Status:** done

- [x] With success rate 1.0: create → one Webhook with `succeeded` → GET shows `succeeded` and `processed_at`
- [x] With success rate 0.0: one Webhook with `failed`, and the Payment is `failed`
- [x] The Outbox event gets `published_at` after a create
- [x] Publishing the same event twice runs the Emulator once and keeps the final status unchanged
- [x] Unit tests: the Emulator with a seeded random source and fake sleep respects the success rate and delay bounds
- [x] System tests use a stub Webhook receiver; `scripts/check` passes

## Comments

- 2026-11-10: Done. Reviewed (1 blocker fixed: unroutable publishes were marked published; now only a Basic.Ack counts). Delivery failures go through on_delivery_failure(message, error), which still logs and acks until ticket 05. Compose smoke test: pending -> succeeded. scripts/check: 74 passed under Podman.
