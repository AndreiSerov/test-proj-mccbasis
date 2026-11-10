# 03: Idempotency

**What to build:** Retried creates never produce a second Payment. The Request hash is a SHA-256 of a canonical form of the validated body (sorted keys, amount normalized to 2 decimals, canonical URL string), stored on the Payment. A create whose Idempotency key already exists returns 202 with the original Payment's body when the Request hash matches, and 409 when it differs. Concurrent creates with the same key are resolved by the unique constraint: the loser re-reads the row and applies the same rule.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Same key and same body returns the same `payment_id` and `created_at`; still exactly one Payment and one Outbox event
- [ ] Same key and a different body returns 409
- [ ] Concurrent creates with the same key and body yield one Payment, one Outbox event, and identical responses
- [ ] Unit tests: the Request hash is equal across metadata key order and `10.0`/`10.00`, and differs for different bodies
- [ ] `scripts/check` passes
