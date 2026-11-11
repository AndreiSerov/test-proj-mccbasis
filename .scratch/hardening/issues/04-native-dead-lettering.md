# 04: Native dead-lettering on `payments.new`

**What to build:** Today only the Retry queues have a dead-letter exchange; `payments.new` itself has none, and every path to the DLQ is the consumer republishing the message. A message the consumer rejects outside that path (FastStream rejecting on an unexpected error, or an ack/nack call that raises) is lost (review F5). Declare `payments.new` with `x-dead-letter-exchange: payments.dlx` and `x-dead-letter-routing-key: payments.new.dlq`, so any rejected-without-requeue message lands in the DLQ by the broker itself. Keep the existing republish path for retries and for Unprocessable messages (it is what carries `x-attempt` and `x-last-error`).

Changing a queue's arguments makes RabbitMQ refuse the redeclare (`PRECONDITION_FAILED`) against an existing queue declared without them. Compose's RabbitMQ has no volume, so a fresh `up` is fine; say in the README that an existing broker needs `payments.new` deleted once (or re-created) when upgrading.

A delivery limit for a poison message that crashes the whole process needs a quorum queue (`x-delivery-limit`); that is out of scope, list it under README "Possible extensions".

**Blocked by:** 03

**Status:** done

- [x] `payments.new` is declared with the dead-letter arguments; a test that rejects a message from it without requeue finds the message in `payments.new.dlq`
- [x] Retry and Unprocessable paths behave as before (existing tests pass)
- [x] README topology line, upgrade note and possible extensions are updated; GLOSSARY DLQ entry mentions both ways in
- [x] `scripts/check` passes

## Comments

2026-11-11
- `PAYMENTS_NEW_QUEUE` (the one object the FastStream subscriber declares) now carries the dead-letter arguments, so the subscriber and the topology agree. The retry/DLQ republish path in `retry.py` is unchanged.
- Declaration order: `declare_retry_topology` (DLX, DLQ, Retry queues) runs in `on_startup`, before the subscriber starts, so `payments.dlx` exists before `payments.new` can dead-letter. (Without the DLX a dead-letter would be dropped silently by the broker.)
- Test helpers declared `payments.new` without arguments (`clean_slate`, the unroutable-event test), which would raise PRECONDITION_FAILED; they now use passive declares / `_purge_if_exists`.
- FastStream ack behaviour (faststream `AcknowledgementMiddleware`, default `AckPolicy.REJECT_ON_ERROR` for the subscriber): an exception escaping the handler calls `message.reject()` which is `reject(requeue=False)`; `asyncio.CancelledError` is not rejected. With the new arguments that now dead-letters through the broker (previously dropped). Our handler catches `Exception` itself and routes via `FailureRouter`, so this only triggers when `FailureRouter` itself raises (e.g. the `nack`/`ack` call fails). Such messages arrive in the DLQ without `x-attempt`/`x-last-error`, but with RabbitMQ's `x-death` headers. Not tested via the app: only the broker-level reject (new test `test_message_rejected_without_requeue_is_dead_lettered_by_the_broker`).
- Not tested: upgrade of a broker holding the old queue (documented in README only); quorum-queue delivery limit (out of scope, listed in Possible extensions).
