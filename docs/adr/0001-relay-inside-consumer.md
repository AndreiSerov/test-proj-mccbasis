# The outbox Relay runs inside the consumer process

The task's compose file lists only `postgres`, `rabbitmq`, `api` and `consumer`, so the Relay that publishes Outbox events runs as an asyncio task in the consumer process instead of a separate service. It claims rows with `SELECT … FOR UPDATE SKIP LOCKED` (batch 100, 0.5 s poll), publishes with publisher confirms, then sets `published_at`, so delivery to the broker is at least once and more consumer replicas can run safely.

## Considered Options

- A separate `outbox-relay` service: cleaner separation, but adds a runtime service the task does not list.
- Publishing from the API after commit: loses events if the API dies between commit and publish, which is the problem the outbox exists to solve.
