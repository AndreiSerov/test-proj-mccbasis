# test-proj-mccbasis

Async payment processing microservice: a FastAPI API and a FastStream (RabbitMQ) consumer sharing PostgreSQL, using the outbox pattern. This is a walking skeleton; the full README comes with ticket 07.

## Run

Podman is the default container engine; Docker works the same way.

```
scripts/compose up --build                          # podman compose (docker compose if podman is missing)
CONTAINER_ENGINE=docker scripts/compose up --build  # force Docker
```

`podman compose up --build` or `docker compose up --build` directly works too. With Podman on macOS, start the VM first (`podman machine start`).

- API on http://localhost:8000 (`GET /health`; protected routes use `X-API-Key: dev-api-key`)
- RabbitMQ management UI on http://localhost:15672 (guest / guest)

## Check

```
scripts/check
```

Runs ruff, ruff format --check, pyright and pytest. Integration tests start Postgres and RabbitMQ with testcontainers on the same engine choice (`CONTAINER_ENGINE`, default Podman; an explicit `DOCKER_HOST` wins). Without a running engine they are skipped with a reason.

Podman notes:
- On rootless Podman on Linux, testcontainers' Ryuk reaper may fail to start; set `TESTCONTAINERS_RYUK_DISABLED=true`.
- If image pulls fail with `docker-credential-desktop not installed`, remove the leftover `"credsStore": "desktop"` line from `~/.docker/config.json`.
