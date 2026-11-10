# 06: Operability

**What to build:** The consumer is fast enough and safe to deploy, and both processes are traceable. The single consumer process handles up to `CONSUMER_CONCURRENCY` messages at once (default 10), with prefetch to match. On SIGTERM the Relay loop stops and in-flight messages finish before exit; unacked messages are redelivered safely. API and consumer log JSON through stdlib `logging`, with a `payment_id` field on every record about a Payment.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] With concurrency 10 and a 1 s Emulator delay, 10 Payments finish in well under 10 s; with concurrency 1 they are processed one at a time
- [ ] Stopping the consumer during processing finishes in-flight messages, and a restart processes the rest with no duplicate Emulator runs
- [ ] Log records are valid JSON and include `payment_id` where one applies
- [ ] `scripts/check` passes
