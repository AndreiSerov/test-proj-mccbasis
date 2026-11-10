import os
import subprocess
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import ROOT


def _alembic(url: str, *args: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "RABBITMQ_URL": "amqp://unused@localhost/",
        "API_KEY": "unused",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True
    )


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


async def _assert_constraints(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        unique = (
            (
                await conn.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'payments'::regclass AND contype = 'u'"
                    )
                )
            )
            .scalars()
            .all()
        )
        index = (
            await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'outbox' AND indexname = 'ix_outbox_unpublished'"
                )
            )
        ).scalar_one()
    await engine.dispose()
    assert unique == ["UNIQUE (idempotency_key)"]
    assert "WHERE (published_at IS NULL)" in index


async def test_migration_upgrades_and_downgrades_cleanly(postgres_url: str) -> None:
    name = f"migr_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(postgres_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(postgres_url).set(database=name).render_as_string(False)

    try:
        _alembic(url, "upgrade", "head")
        assert {"payments", "outbox"} <= await _tables(url)
        await _assert_constraints(url)

        _alembic(url, "downgrade", "base")
        remaining = await _tables(url)
        assert not {"payments", "outbox"} & remaining

        engine = create_async_engine(url)
        async with engine.connect() as conn:
            types = (
                await conn.execute(
                    text("SELECT typname FROM pg_type WHERE typname LIKE 'payment_%'")
                )
            ).all()
        await engine.dispose()
        assert types == []

        _alembic(url, "upgrade", "head")
        assert {"payments", "outbox"} <= await _tables(url)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
