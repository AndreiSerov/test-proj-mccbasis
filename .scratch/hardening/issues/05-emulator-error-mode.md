# 05: Emulator error mode

**What to build:** The task's "90% успех, 10% ошибка" has two readings. We read the 10% as a Decline (ADR-0003): a business outcome, `failed`, sent by Webhook, never retried. A reviewer who reads it as a technical error expects those 10% to go through retry and the DLQ. Support both, keeping the Decline reading as the default:

- `EMULATOR_FAILURE_MODE=decline` (default): unchanged behaviour.
- `EMULATOR_FAILURE_MODE=error`: the Emulator's 10% outcome is an Emulator error, a Delivery failure. The Payment stays `pending`, nothing is committed, and the message goes through the Retry queues; each retry runs the Emulator again (90% chance it succeeds). After the last attempt the message is parked in the DLQ with `x-last-error` naming the Emulator error, and the Payment stays `pending` (as with a DB outage; re-driving is the documented manual step). No Webhook is sent for an attempt that hit the error.

Add "Emulator error" to `GLOSSARY.md` (and adjust "Decline" and "Delivery failure"), extend ADR-0003 with the mode and why the default stays `decline`, add the setting to Settings, compose forwarding and the README configuration table, and add a README demo step that shows retries and the DLQ with `EMULATOR_FAILURE_MODE=error EMULATOR_SUCCESS_RATE=0`.

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [ ] With `decline`, a 0% success rate gives `failed` Payments, one Webhook each, nothing in the DLQ (existing behaviour, still tested)
- [ ] With `error` and a 0% success rate, the Payment stays `pending`, no Webhook is sent, the Emulator runs 3 times, and the message is in the DLQ with an Emulator-error `x-last-error`
- [ ] With `error` and an Emulator that fails once then succeeds, the Payment ends `succeeded` with one Webhook
- [ ] An unknown value for `EMULATOR_FAILURE_MODE` fails at start-up
- [ ] `scripts/check` passes
