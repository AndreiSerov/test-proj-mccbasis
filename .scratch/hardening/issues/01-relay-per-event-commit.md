# 01: Relay marks each event as it is confirmed

**What to build:** Today the Relay publishes a whole batch inside one transaction. If the broker refuses event N, the transaction rolls back and events 1..N-1, which the broker already accepted, are published again a second later; a persistent failure on one event republishes the events before it forever (review F1, probe: 5 events, event 4 nacked 3 times → events 1-3 published 4 times each). Fix it so that an event the broker confirmed is never published again because of a later event's failure: `published_at` for every confirmed event is durable before the Relay gives up on the batch. The failing event and the ones after it are retried on the next iteration, in id order, after the existing error pause.

Also give each publish a timeout (review F3): a publish that gets no confirm within a bounded time (a setting, `RELAY_PUBLISH_TIMEOUT`, default 5 s) counts as a failure, so a blocked broker connection cannot hang the Relay while it holds row locks.

Keep `FOR UPDATE SKIP LOCKED` claiming, so several consumer replicas stay safe. Choose between committing per event and committing the confirmed prefix then stopping the batch; say which in the ticket comment.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A test with a publish that fails on one event in a batch shows every event before it published exactly once, and the failing event and the ones after it published after the failure clears, in id order
- [x] A test with a publish that never confirms shows the Relay iteration fails within `RELAY_PUBLISH_TIMEOUT` instead of hanging, and a later iteration publishes the event
- [x] `RELAY_PUBLISH_TIMEOUT` is in Settings, forwarded by compose, and in the README configuration table
- [x] `scripts/check` passes

## Comments

2026-11-11: Chose "commit the confirmed prefix, then stop the batch" over per-event commit. The claim transaction keeps its `FOR UPDATE SKIP LOCKED` row locks for the whole batch, so a replica cannot grab the unpublished tail and double-publish it; per-event commit would release the locks after the first event. On a publish failure or timeout, `publish_batch` marks the events confirmed so far, commits, then re-raises; `_run` applies the existing error pause and the next iteration retries the failed event onward in id order. Each publish is wrapped in `asyncio.wait_for(..., RELAY_PUBLISH_TIMEOUT)` (default 5 s; Settings, compose, README table). Tests: `tests/system/test_relay.py` (failure mid-batch, hung publish). `scripts/check`: 101 passed. Not tested: multi-replica contention on the locks.

2026-11-11 (review fixes): `publish_batch` now stops the batch early when the Relay is stopping (checked before each publish), so a normal `stop` commits the confirmed prefix. The per-event `except` is `BaseException`, so a cancellation (`stop` after `STOP_TIMEOUT`) also breaks out, commits the prefix and then re-raises; a cancel is delivered once, so the commit can still be awaited, and re-raising keeps the task cancelled (no `uncancel` needed). If the commit itself fails after a publish failure, the original error is logged and chained as `__cause__`. Tests added: timeout on a later event, cancel mid-batch, stop mid-batch, commit error after publish error. README guarantees note that a timed-out publish may already be in the broker (at least once). Not tested: a second cancel arriving during the commit.
