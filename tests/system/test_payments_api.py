import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from conftest import API_KEY
from payments.api.app import create_app
from payments.core.settings import Settings
from payments.domain.create_payment import CreatePayment
from payments.domain.request_hash import request_hash

AUTH = {"X-API-Key": API_KEY}


def valid_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "amount": "10.50",
        "currency": "USD",
        "description": "Order 42",
        "metadata": {"order_id": 42},
        "webhook_url": "https://client.example/hooks/payments",
    }
    return {**body, **overrides}


def headers(key: str | None = None) -> dict[str, str]:
    return {**AUTH, "Idempotency-Key": key or str(uuid.uuid4())}


@pytest.fixture
async def client(migrated_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(migrated_settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


@pytest.fixture
async def db(migrated_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_settings.database_url)
    yield engine
    await engine.dispose()


async def test_create_returns_202_pending_and_get_returns_all_fields(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers()
    )

    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "pending"
    payment_id = body["payment_id"]

    fetched = await client.get(f"/api/v1/payments/{payment_id}", headers=AUTH)

    assert fetched.status_code == 200
    got = fetched.json()
    assert got["id"] == payment_id
    assert got["amount"] == "10.50"
    assert got["currency"] == "USD"
    assert got["description"] == "Order 42"
    assert got["metadata"] == {"order_id": 42}
    assert got["status"] == "pending"
    assert got["webhook_url"] == "https://client.example/hooks/payments"
    assert got["created_at"] == body["created_at"]
    assert got["processed_at"] is None
    assert "request_hash" not in got


async def test_metadata_defaults_to_empty_object(client: httpx.AsyncClient) -> None:
    payload = valid_body()
    del payload["metadata"]
    created = await client.post("/api/v1/payments", json=payload, headers=headers())
    fetched = await client.get(
        f"/api/v1/payments/{created.json()['payment_id']}", headers=AUTH
    )

    assert fetched.json()["metadata"] == {}


async def test_amount_is_a_string_even_when_sent_as_a_number(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/payments", json=valid_body(amount=5), headers=headers()
    )
    fetched = await client.get(
        f"/api/v1/payments/{created.json()['payment_id']}", headers=AUTH
    )

    assert fetched.json()["amount"] == "5.00"


async def test_each_create_leaves_one_payment_and_one_unpublished_outbox_event(
    client: httpx.AsyncClient, db: AsyncEngine
) -> None:
    created = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers()
    )
    payment_id = created.json()["payment_id"]

    async with db.connect() as conn:
        payment_count = (
            await conn.execute(
                text("SELECT count(*) FROM payments WHERE id = :id"),
                {"id": uuid.UUID(payment_id)},
            )
        ).scalar_one()
        rows = (
            await conn.execute(
                text(
                    "SELECT event_type, payload, published_at FROM outbox "
                    "WHERE aggregate_id = :id"
                ),
                {"id": uuid.UUID(payment_id)},
            )
        ).all()

    assert payment_count == 1
    assert len(rows) == 1
    event_type, payload, published_at = rows[0]
    assert event_type == "payments.new"
    assert payload["payment_id"] == payment_id
    assert published_at is None


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/v1/payments"), ("GET", f"/api/v1/payments/{uuid.uuid4()}")],
)
@pytest.mark.parametrize("key_headers", [{}, {"X-API-Key": "wrong"}])
async def test_missing_or_wrong_api_key_returns_401(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    key_headers: dict[str, str],
) -> None:
    response = await client.request(
        method,
        path,
        headers={**key_headers, "Idempotency-Key": "k"},
        json=valid_body() if method == "POST" else None,
    )

    assert response.status_code == 401


async def test_health_stays_open(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "0"},
        {"amount": "-1.00"},
        {"amount": "10.001"},
        {"amount": "abc"},
        {"currency": "GBP"},
        {"description": ""},
        {"description": "x" * 256},
        {"webhook_url": "not-a-url"},
        {"webhook_url": "ftp://client.example/hook"},
    ],
)
async def test_invalid_body_returns_422(
    client: httpx.AsyncClient, overrides: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(**overrides), headers=headers()
    )

    assert response.status_code == 422


async def test_description_boundaries_are_accepted(client: httpx.AsyncClient) -> None:
    for description in ("x", "x" * 255):
        response = await client.post(
            "/api/v1/payments",
            json=valid_body(description=description),
            headers=headers(),
        )
        assert response.status_code == 202


async def test_missing_idempotency_key_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=valid_body(), headers=AUTH)

    assert response.status_code == 422


async def test_unknown_payment_id_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/payments/{uuid.uuid4()}", headers=AUTH)

    assert response.status_code == 404


async def test_non_uuid_payment_id_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/payments/not-a-uuid", headers=AUTH)

    assert response.status_code == 422


async def test_nul_character_in_description_returns_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(description="a\x00b"), headers=headers()
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "metadata",
    [
        {"note": "a\x00b"},
        {"a\x00b": "value"},
        {"nested": {"list": ["ok", "a\x00b"]}},
    ],
)
async def test_nul_character_in_metadata_returns_422(
    client: httpx.AsyncClient, metadata: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(metadata=metadata), headers=headers()
    )

    assert response.status_code == 422


async def test_idempotency_key_longer_than_255_returns_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers("k" * 256)
    )

    assert response.status_code == 422


async def test_idempotency_key_of_255_characters_is_accepted(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers("k" * 255)
    )

    assert response.status_code == 202


async def test_amount_with_trailing_zero_beyond_two_decimals_is_accepted_as_two(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/payments", json=valid_body(amount="10.100"), headers=headers()
    )
    fetched = await client.get(
        f"/api/v1/payments/{created.json()['payment_id']}", headers=AUTH
    )

    assert created.status_code == 202
    assert fetched.json()["amount"] == "10.10"


async def test_amount_with_a_third_significant_decimal_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/payments", json=valid_body(amount="10.123"), headers=headers()
    )

    assert response.status_code == 422


async def _counts(db: AsyncEngine, key: str) -> tuple[int, int]:
    async with db.connect() as conn:
        payments = (
            await conn.execute(
                text("SELECT count(*) FROM payments WHERE idempotency_key = :k"),
                {"k": key},
            )
        ).scalar_one()
        events = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id IN "
                    "(SELECT id FROM payments WHERE idempotency_key = :k)"
                ),
                {"k": key},
            )
        ).scalar_one()
    return payments, events


async def test_same_key_and_body_replays_the_original_payment(
    client: httpx.AsyncClient, db: AsyncEngine
) -> None:
    key = str(uuid.uuid4())
    first = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers(key)
    )
    second = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers(key)
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert await _counts(db, key) == (1, 1)


async def test_replay_returns_the_stored_status_not_pending(
    client: httpx.AsyncClient, db: AsyncEngine
) -> None:
    key = str(uuid.uuid4())
    first = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers(key)
    )
    async with db.begin() as conn:
        await conn.execute(
            text("UPDATE payments SET status = 'succeeded' WHERE idempotency_key = :k"),
            {"k": key},
        )

    replay = await client.post(
        "/api/v1/payments", json=valid_body(), headers=headers(key)
    )

    assert replay.status_code == 202
    assert replay.json()["payment_id"] == first.json()["payment_id"]
    assert replay.json()["created_at"] == first.json()["created_at"]
    assert replay.json()["status"] == "succeeded"


async def test_replay_ignores_metadata_key_order_and_amount_formatting(
    client: httpx.AsyncClient, db: AsyncEngine
) -> None:
    key = str(uuid.uuid4())
    first = await client.post(
        "/api/v1/payments",
        json=valid_body(amount="10.5", metadata={"a": 1, "b": 2}),
        headers=headers(key),
    )
    second = await client.post(
        "/api/v1/payments",
        json=valid_body(amount="10.50", metadata={"b": 2, "a": 1}),
        headers=headers(key),
    )

    assert second.status_code == 202
    assert second.json() == first.json()
    assert await _counts(db, key) == (1, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "10.51"},
        {"currency": "EUR"},
        {"description": "Order 43"},
        {"metadata": {"order_id": 43}},
        {"webhook_url": "https://client.example/hooks/other"},
    ],
)
async def test_same_key_with_a_different_body_returns_409(
    client: httpx.AsyncClient, db: AsyncEngine, overrides: dict[str, Any]
) -> None:
    key = str(uuid.uuid4())
    await client.post("/api/v1/payments", json=valid_body(), headers=headers(key))

    conflict = await client.post(
        "/api/v1/payments", json=valid_body(**overrides), headers=headers(key)
    )

    assert conflict.status_code == 409
    assert "Idempotency-Key" in conflict.json()["detail"]
    assert await _counts(db, key) == (1, 1)


@asynccontextmanager
async def separate_clients(
    settings: Settings, n: int
) -> AsyncIterator[list[httpx.AsyncClient]]:
    """n clients, each with its own app and connection pool."""
    apps = [create_app(settings) for _ in range(n)]
    clients = [
        httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test")
        for a in apps
    ]
    try:
        yield clients
    finally:
        for c in clients:
            await c.aclose()
        for a in apps:
            await a.state.engine.dispose()


async def test_concurrent_creates_with_the_same_key_make_one_payment(
    migrated_settings: Settings, db: AsyncEngine
) -> None:
    key = str(uuid.uuid4())
    async with separate_clients(migrated_settings, 10) as clients:
        responses = await asyncio.gather(
            *(
                c.post("/api/v1/payments", json=valid_body(), headers=headers(key))
                for c in clients
            )
        )

    assert [r.status_code for r in responses] == [202] * 10
    assert len({r.text for r in responses}) == 1
    assert await _counts(db, key) == (1, 1)


async def test_concurrent_creates_with_the_same_key_and_different_bodies(
    migrated_settings: Settings, db: AsyncEngine
) -> None:
    key = str(uuid.uuid4())
    async with separate_clients(migrated_settings, 6) as clients:
        responses = await asyncio.gather(
            *(
                c.post(
                    "/api/v1/payments",
                    json=valid_body(description=f"Order {i % 2}"),
                    headers=headers(key),
                )
                for i, c in enumerate(clients)
            )
        )

    assert sorted({r.status_code for r in responses}) == [202, 409]
    assert await _counts(db, key) == (1, 1)


async def _post_while_rival_insert_is_pending(
    settings: Settings,
    db: AsyncEngine,
    rival_body: dict[str, Any],
    post_body: dict[str, Any],
) -> tuple[httpx.Response, uuid.UUID]:
    """Force the race: a rival holds an uncommitted insert of the key while the POST
    misses its lookup and blocks on the unique index; then the rival commits."""
    key = str(uuid.uuid4())
    payment_id = uuid.uuid4()
    rival_hash = request_hash(CreatePayment.model_validate(rival_body))
    async with (
        separate_clients(settings, 1) as (client,),
        db.connect() as rival,
        db.connect() as monitor,
    ):
        # Autocommit, so every poll sees a fresh pg_stat_activity snapshot.
        await monitor.execution_options(isolation_level="AUTOCOMMIT")
        await rival.begin()
        await rival.execute(
            text(
                "INSERT INTO payments (id, amount, currency, description, metadata, "
                "status, idempotency_key, request_hash, webhook_url, created_at) "
                "VALUES (:id, 10.50, 'USD', 'Order 42', '{}', 'pending', :k, :h, "
                "'https://client.example/hooks/payments', now())"
            ),
            {"id": payment_id, "k": key, "h": rival_hash},
        )
        await rival.execute(
            text(
                "INSERT INTO outbox (aggregate_id, event_type, payload, created_at) "
                "VALUES (:id, 'payments.new', '{}', now())"
            ),
            {"id": payment_id},
        )
        post = asyncio.create_task(
            client.post("/api/v1/payments", json=post_body, headers=headers(key))
        )
        try:
            async with asyncio.timeout(10):
                while True:
                    blocked = (
                        await monitor.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE wait_event_type = 'Lock' "
                                "AND pid <> pg_backend_pid()"
                            )
                        )
                    ).scalar_one()
                    if blocked:
                        break
                    await asyncio.sleep(0.05)
            await rival.commit()
        except BaseException:
            post.cancel()
            raise
        return await post, payment_id


async def test_losing_the_insert_race_replays_the_winner(
    migrated_settings: Settings, db: AsyncEngine
) -> None:
    response, payment_id = await _post_while_rival_insert_is_pending(
        migrated_settings, db, valid_body(), valid_body()
    )

    assert response.status_code == 202
    assert response.json()["payment_id"] == str(payment_id)


async def test_losing_the_insert_race_with_a_different_body_returns_409(
    migrated_settings: Settings, db: AsyncEngine
) -> None:
    response, _ = await _post_while_rival_insert_is_pending(
        migrated_settings, db, valid_body(description="Other"), valid_body()
    )

    assert response.status_code == 409
