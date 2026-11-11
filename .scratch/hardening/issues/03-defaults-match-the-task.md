# 03: Defaults that match the task's letter

**What to build:** Close the deviations a strict reader of `docs/task/task.pdf` would flag, so they stop being interpretations:

1. **Three attempts.** The task says "Retry: 3 попытки" and "DLQ для сообщений, не обработанных после 3 попыток". Change the default `RETRY_DELAYS` to `1,2`: three Webhook calls in total (first attempt, retry after 1 s, retry after 2 s), then the DLQ. The delay stays exponential. `x-attempt` in the DLQ is then `3`. Update the glossary's DLQ and Retry queue entries (Retry queues are now `payments.new.retry.{1s,2s}` by default), ADR-0002 if it names the delays, and the README (the attempts design decision, the DLQ walkthrough log lines and headers, the configuration table).
2. **API key on every endpoint except `/health`.** The task says "X-API-Key для всех эндпоинтов". `/docs`, `/redoc` and `/openapi.json` now require `X-API-Key` too (a browser can't send the header, so serving them behind the key is enough; disabling them outright is also acceptable, pick one and say why in the ticket comment). `/health` stays open for container healthchecks; that is the one stated exception.
3. **No empty API key.** `API_KEY` must be non-empty: Settings fails at start-up if it is empty or only whitespace (today `compare_digest(b"", b"")` lets every request in when it is set to `""`).
4. **One name for the id.** `GET /api/v1/payments/{payment_id}` returns the id as `payment_id`, the same name `POST` uses, instead of `id`.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] With default settings, a Webhook that always fails is called exactly 3 times, then the message is in `payments.new.dlq` with `x-attempt: 3`
- [x] `GET /docs` and `GET /openapi.json` without the key return `401` (or `404` if disabled); `/health` returns `200` without it
- [x] Settings with `API_KEY=""` or `"  "` raise at start-up
- [x] `GET` returns `payment_id`, not `id`; README examples match
- [x] `scripts/check` passes

## Comments

2026-11-11:
- Docs choice: `/docs`, `/redoc`, `/openapi.json` are served behind `X-API-Key` (default docs disabled, protected routes added via `get_swagger_ui_html`, `get_redoc_html`, `get_openapi`). Reason: reviewers can still use them with curl or a header-injecting extension, and the task's "all endpoints" is met literally. Swagger UI in a plain browser cannot fetch `openapi.json` (no header); documented in the README.
- `Settings.api_key` has a validator rejecting empty or whitespace-only values.
- `PaymentOut` exposes `payment_id` (read from the model's `id`).
- Default `RETRY_DELAYS=1,2`; glossary (DLQ, Retry queue), ADR-0002 and README updated (attempts decision, DLQ walkthrough with `x-attempt: 3`, config and API tables, GET example).
- Tests: new unit tests `tests/unit/test_settings.py` and `tests/unit/test_api_docs.py`. System tests now use `SHORT_DELAYS=(0.1, 0.2)` and expect 3 Webhook calls and `x-attempt: 3`. The default-delay criterion is asserted at the Settings level (default is `(1, 2)`); the system tests inject short delays, so a 1 s + 2 s end-to-end run on defaults was not done.
- `scripts/check`: ruff, format, pyright clean; 167 passed (system tests ran against Podman containers).
