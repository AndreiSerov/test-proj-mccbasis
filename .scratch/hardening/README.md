# Hardening after the final review

Source: the clean-context final review (findings F1-F7) and the ambiguity pass over `docs/task/task.pdf`, 2026-11-11. The original spec, `.scratch/payment-processing/spec.md`, still applies; these tickets change it only where they say so.

| # | Ticket | Blocked by |
|---|---|---|
| 01 | Relay marks each event as it is confirmed | None |
| 02 | Webhook URLs cannot reach internal hosts, and are masked in logs | None |
| 03 | Defaults that match the task's letter | None |
| 04 | Native dead-lettering on `payments.new` | 03 |
| 05 | Emulator error mode | 03, 04 |
| 06 | README: interpretation of the task | 01-05 |

Ship: one commit per ticket on `main`, pushed once all are done (the user asked for main, no PR).
