# 01: Relay marks each event as it is confirmed

**What to build:** Today the Relay publishes a whole batch inside one transaction. If the broker refuses event N, the transaction rolls back and events 1..N-1, which the broker already accepted, are published again a second later; a persistent failure on one event republishes the events before it forever (review F1, probe: 5 events, event 4 nacked 3 times → events 1-3 published 4 times each). Fix it so that an event the broker confirmed is never published again because of a later event's failure: `published_at` for every confirmed event is durable before the Relay gives up on the batch. The failing event and the ones after it are retried on the next iteration, in id order, after the existing error pause.

Also give each publish a timeout (review F3): a publish that gets no confirm within a bounded time (a setting, `RELAY_PUBLISH_TIMEOUT`, default 5 s) counts as a failure, so a blocked broker connection cannot hang the Relay while it holds row locks.

Keep `FOR UPDATE SKIP LOCKED` claiming, so several consumer replicas stay safe. Choose between committing per event and committing the confirmed prefix then stopping the batch; say which in the ticket comment.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A test with a publish that fails on one event in a batch shows every event before it published exactly once, and the failing event and the ones after it published after the failure clears, in id order
- [ ] A test with a publish that never confirms shows the Relay iteration fails within `RELAY_PUBLISH_TIMEOUT` instead of hanging, and a later iteration publishes the event
- [ ] `RELAY_PUBLISH_TIMEOUT` is in Settings, forwarded by compose, and in the README configuration table
- [ ] `scripts/check` passes
