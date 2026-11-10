# 06: Operability

**What to build:** The consumer is fast enough and safe to deploy, and both processes are traceable. The single consumer process handles up to `CONSUMER_CONCURRENCY` messages at once (default 10), with prefetch to match. On SIGTERM the Relay loop stops and in-flight messages finish before exit; unacked messages are redelivered safely. API and consumer log JSON through stdlib `logging`, with a `payment_id` field on every record about a Payment.

**Blocked by:** 04

**Status:** done

- [x] With concurrency 10 and a 1 s Emulator delay, 10 Payments finish in well under 10 s; with concurrency 1 they are processed one at a time
- [x] Stopping the consumer during processing finishes in-flight messages, and a restart processes the rest with no duplicate Emulator runs
- [x] Log records are valid JSON and include `payment_id` where one applies
- [x] `scripts/check` passes

## Comments

- 2026-11-10: Done. Reviewed (no blockers; 6 fixes). Added GRACEFUL_TIMEOUT (30 s) beyond the spec's settings, approved; Relay stop is capped at 5 s and the compose grace period is 45 s. The Relay's cancel-after-timeout fallback is not tested. Compose smoke test: all log lines JSON, consumer stop exits 0. scripts/check: 97 passed under Podman.
