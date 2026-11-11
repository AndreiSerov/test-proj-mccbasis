# 04: Native dead-lettering on `payments.new`

**What to build:** Today only the Retry queues have a dead-letter exchange; `payments.new` itself has none, and every path to the DLQ is the consumer republishing the message. A message the consumer rejects outside that path (FastStream rejecting on an unexpected error, or an ack/nack call that raises) is lost (review F5). Declare `payments.new` with `x-dead-letter-exchange: payments.dlx` and `x-dead-letter-routing-key: payments.new.dlq`, so any rejected-without-requeue message lands in the DLQ by the broker itself. Keep the existing republish path for retries and for Unprocessable messages (it is what carries `x-attempt` and `x-last-error`).

Changing a queue's arguments makes RabbitMQ refuse the redeclare (`PRECONDITION_FAILED`) against an existing queue declared without them. Compose's RabbitMQ has no volume, so a fresh `up` is fine; say in the README that an existing broker needs `payments.new` deleted once (or re-created) when upgrading.

A delivery limit for a poison message that crashes the whole process needs a quorum queue (`x-delivery-limit`); that is out of scope, list it under README "Possible extensions".

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] `payments.new` is declared with the dead-letter arguments; a test that rejects a message from it without requeue finds the message in `payments.new.dlq`
- [ ] Retry and Unprocessable paths behave as before (existing tests pass)
- [ ] README topology line, upgrade note and possible extensions are updated; GLOSSARY DLQ entry mentions both ways in
- [ ] `scripts/check` passes
