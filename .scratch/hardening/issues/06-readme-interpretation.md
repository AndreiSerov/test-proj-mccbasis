# 06: README: interpretation of the task

**What to build:** A reviewer should find every place where we read the task one way rather than another without hunting through "Design decisions". Add a section near the top of the README, right after the architecture overview, titled "Interpretation of the task": a table with the task's phrase (quoted in Russian, with an English gloss), how we read it, and why, linking the ADR where one exists. Cover at least:

- "10% ошибка": Decline by default, `EMULATOR_FAILURE_MODE=error` for the other reading
- "3 попытки" / "DLQ после 3 попыток": 3 calls in total
- "X-API-Key для всех эндпоинтов": every endpoint but `/health`
- "Реализует повторные попытки при ошибках отправки": what is retried (Webhook non-2xx/timeout, DB errors, Emulator errors in `error` mode) and that any non-2xx, including 4xx, is retried
- Webhook outcome vs Payment status: an undelivered Webhook does not change the Payment's status
- "Даты обработки": `processed_at` is when the Emulator result was committed
- The Webhook body, not specified by the task
- Idempotency: replay returns `202` with the current status, same key with a different body is `409`, missing key is `422`, keys are kept forever
- The Relay lives in the consumer, because the task's compose list has no relay service
- `amount` (> 0, at most 2 decimal places, any currency) and `metadata` (a JSON object)
- Docker and Podman: `docker compose up` works as is

Then remove the duplicated interpretation bullets from "Design decisions" (keep the design reasons there), and check the whole README still matches the code after tickets 01-05: commands, example outputs, configuration table, topology.

**Blocked by:** 01, 02, 03, 04, 05

**Status:** done

- [x] The section exists near the top and covers every item above
- [x] No README statement contradicts the code (defaults, field names, queue names, attempt counts)
- [x] `scripts/check` passes

## Comments

2026-11-11: Added "Interpretation of the task" (11-row table, after Architecture) and removed the API key scope and Attempts bullets from Design decisions; trimmed the Relay and Decline bullets to the design reasons. Verified live with `scripts/compose --profile demo up -d --build`: every "Try it" command (create 202, idempotent replay, 409, GET with `payment_id`, Webhook in the sink log, 401 on `/docs` without key, 200 on `/health`, 200 on `/openapi.json` with key, 422/401/404 cases), the `?fail=1` DLQ run (3 sink calls about 1 s and 2 s apart, the three retry log lines, DLQ headers `x-attempt: 3`), the DLQ re-drive (message taken off, republished, `{"routed":true}`, reprocessed from attempt 1), and the error-mode demo (consumer restarted with `EMULATOR_FAILURE_MODE=error EMULATOR_SUCCESS_RATE=0`: 3 attempts, Payment `pending` with `processed_at: null`, no Webhook, `x-last-error: EmulatorError: ...`). Then `down -v`. Mismatches fixed: `x-last-error` shows the Webhook URL with the query masked as `?…` (README showed `?fail=1`); the architecture diagram said "after 3 retries" and the overview said retry queues "1 s, 2 s, 4 s" (now one attempt plus two retries, 1 s then 2 s). No code bugs found. `scripts/check`: 181 passed. Not done: `docs/task/task.pdf` could not be rendered (no poppler), so the quoted task phrases come from the tickets.
