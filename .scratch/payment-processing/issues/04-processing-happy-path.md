# 04: Processing, happy path

**What to build:** An accepted Payment is processed and its client is notified. The consumer declares the `payments` direct exchange and the durable `payments.new` queue. The Relay, an asyncio task in the consumer lifecycle, claims unpublished Outbox events in id order with `FOR UPDATE SKIP LOCKED` (batch and poll interval from settings), publishes them with publisher confirms, and sets `published_at` (ADR-0001). The handler locks the Payment; if `pending` it runs the Emulator (random delay in `[min, max]`, success with probability `success_rate`, injectable random source and sleep), commits `succeeded` or `failed` with `processed_at`, then POSTs the Webhook `{payment_id, status, amount, currency, processed_at, metadata}` with the configured timeout. If the Payment is already final, the Emulator is skipped and only the Webhook is sent. A Decline is a normal outcome (ADR-0003). Retry queues and the DLQ are not part of this ticket.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] With success rate 1.0: create → one Webhook with `succeeded` → GET shows `succeeded` and `processed_at`
- [ ] With success rate 0.0: one Webhook with `failed`, and the Payment is `failed`
- [ ] The Outbox event gets `published_at` after a create
- [ ] Publishing the same event twice runs the Emulator once and keeps the final status unchanged
- [ ] Unit tests: the Emulator with a seeded random source and fake sleep respects the success rate and delay bounds
- [ ] System tests use a stub Webhook receiver; `scripts/check` passes
