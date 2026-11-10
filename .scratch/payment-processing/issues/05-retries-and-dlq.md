# 05: Retries and DLQ

**What to build:** Delivery failures are retried through the broker and finally parked (ADR-0002). The consumer declares the Retry queues `payments.new.retry.1s/2s/4s`, whose TTLs come from `RETRY_DELAYS` and which dead-letter back to `payments` with routing key `payments.new`, and the `payments.dlx` exchange routing to `payments.new.dlq`. On a Delivery failure (Webhook non-2xx or timeout, DB error) the handler publishes the message to the next Retry queue with the attempt header incremented and acks it. After the third retry fails, it publishes the original body to the DLQ with the last error in a header and acks it. An unknown `payment_id` goes straight to the DLQ. A Decline is never retried (ADR-0003).

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Receiver fails twice then succeeds: the Webhook is delivered on the 3rd call, the Emulator ran once, and the status never changed
- [ ] Receiver always fails: 4 Webhook calls in total, then the message is in `payments.new.dlq` with an error header
- [ ] A Webhook timeout counts as a Delivery failure
- [ ] A message for an unknown `payment_id` lands in the DLQ
- [ ] A Decline produces one Webhook and nothing in the DLQ
- [ ] All queues and exchanges are durable and declared automatically on a fresh broker
- [ ] System tests use short `RETRY_DELAYS`; `scripts/check` passes
