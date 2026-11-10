# 07: Demo sink and README

**What to build:** A reviewer can run and evaluate the service in minutes. Compose gains a `webhook-sink` service under `profiles: [demo]`: a tiny app that logs every request it receives and returns 500 when the URL has `?fail=1`. An English README covers a mermaid architecture diagram, how to run (default and demo profile), curl examples (create, idempotent repeat, 409 conflict, get, watching the Webhook in the sink logs, forcing the DLQ with `?fail=1` and seeing it in the RabbitMQ management UI), configuration, how to run the tests, the design decisions (linking the ADRs), the known trade-offs (at-least-once Webhooks and broker delivery, Relay polling), the manual DLQ re-drive, and possible extensions (HMAC Webhook signing, sweeper).

**Blocked by:** 03, 05, 06

**Status:** done

- [x] `docker compose up` still starts only the required services; `docker compose --profile demo up` adds the webhook-sink
- [x] Every README curl example has been run against compose and gives the documented result (manual)
- [x] The README describes the architecture, the guarantees and the trade-offs, consistent with the ADRs and the glossary
- [x] `scripts/check` passes

## Comments

- 2026-11-10: Done. Reviewed together with a final check against the original task (all requirements met; interpretations of API key scope and attempt count stated in the README). Every README example run against the demo stack under Podman, including the single-message DLQ re-drive. Not run with Docker as the engine. scripts/check: 99 passed.
