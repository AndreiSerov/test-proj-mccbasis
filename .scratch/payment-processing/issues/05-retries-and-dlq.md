# 05: Retries and DLQ

**What to build:** Delivery failures are retried through the broker and finally parked (ADR-0002). The consumer declares the Retry queues `payments.new.retry.1s/2s/4s`, whose TTLs come from `RETRY_DELAYS` and which dead-letter back to `payments` with routing key `payments.new`, and the `payments.dlx` exchange routing to `payments.new.dlq`. On a Delivery failure (Webhook non-2xx or timeout, DB error) the handler publishes the message to the next Retry queue with the attempt header incremented and acks it. After the third retry fails, it publishes the original body to the DLQ with the last error in a header and acks it. An unknown `payment_id` goes straight to the DLQ. A Decline is never retried (ADR-0003).

**Blocked by:** 04

**Status:** done

- [x] Receiver fails twice then succeeds: the Webhook is delivered on the 3rd call, the Emulator ran once, and the status never changed
- [x] Receiver always fails: 4 Webhook calls in total, then the message is in `payments.new.dlq` with an error header
- [x] A Webhook timeout counts as a Delivery failure
- [x] A message for an unknown `payment_id` lands in the DLQ
- [x] A Decline produces one Webhook and nothing in the DLQ
- [x] All queues and exchanges are durable and declared automatically on a fresh broker
- [x] System tests use short `RETRY_DELAYS`; `scripts/check` passes

## Comments

- 2026-11-10: Done. Reviewed (no blockers; 6 fixes incl. a pause before requeue when a republish is refused). Compose smoke test: unreachable webhook -> DLQ with x-attempt 4 after ~14 s. A DB error being retried is not tested (shares the Webhook failure path). scripts/check: 85 passed under Podman.
