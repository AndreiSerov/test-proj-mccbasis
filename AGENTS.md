# test-proj-mccbasis

Async payment processing microservice (FastAPI, RabbitMQ/FastStream, PostgreSQL, outbox pattern) - hh.ru test task

Before feature work, read [`docs/agents/workflow.md`](docs/agents/workflow.md). Before any change, follow [`docs/agents/agreements.md`](docs/agents/agreements.md). Before naming anything, read [`GLOSSARY.md`](GLOSSARY.md).

## Agent skills

Install them with `scripts/sync-skills`. They come from the sources pinned in `.claude/skills.toml`.

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The default five role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Done means

`scripts/check` passes. Work needing real hardware, permissions or accounts is marked `manual`, and the user runs it. Report it as not run.

## Conventions

<!-- What's specific to this project and can't be seen in the code. -->

## Gotchas

<!-- Add one when it has actually caught someone out. -->
