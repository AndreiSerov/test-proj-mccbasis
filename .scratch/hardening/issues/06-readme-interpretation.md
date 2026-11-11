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

**Status:** ready-for-agent

- [ ] The section exists near the top and covers every item above
- [ ] No README statement contradicts the code (defaults, field names, queue names, attempt counts)
- [ ] `scripts/check` passes
