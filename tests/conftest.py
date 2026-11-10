"""Harness: Postgres and RabbitMQ via testcontainers; skipped without Docker."""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer

from payments.core.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def _docker_available() -> bool:
    import docker

    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def docker_required() -> None:
    if not _docker_available():
        pytest.skip("Docker is not available: integration tests need testcontainers")


@pytest.fixture(scope="session")
def postgres_url(docker_required: None) -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def rabbitmq_url(docker_required: None) -> Iterator[str]:
    with RabbitMqContainer("rabbitmq:3-management-alpine") as mq:
        host = mq.get_container_host_ip()
        port = mq.get_exposed_port(mq.port)
        yield f"amqp://{mq.username}:{mq.password}@{host}:{port}/"


API_KEY = "test-key"


@pytest.fixture(scope="session")
def migrated_settings(postgres_url: str, rabbitmq_url: str) -> Settings:
    """Settings pointing at the containers, after running migrations."""
    env = {
        **os.environ,
        "DATABASE_URL": postgres_url,
        "RABBITMQ_URL": rabbitmq_url,
        "API_KEY": API_KEY,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    return Settings(
        database_url=postgres_url, rabbitmq_url=rabbitmq_url, api_key=API_KEY
    )
