"""Harness: Postgres and RabbitMQ via testcontainers, skipped without an engine.

The engine is Podman by default (Docker if Podman is missing), or whatever
CONTAINER_ENGINE names. An explicit DOCKER_HOST always wins.
"""

import os
import shutil
import subprocess
import sys
import warnings
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.core.container import DockerContainer

from payments.core.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def _podman(*args: str) -> str:
    result = subprocess.run(["podman", *args], capture_output=True, text=True)
    return (
        result.stdout.strip().removeprefix("unix://") if result.returncode == 0 else ""
    )


def _use_podman() -> None:
    """Point testcontainers at the local Podman's Docker-compatible API socket.

    On Linux Podman runs natively and both paths are the same socket. On macOS
    and Windows it runs in a VM: DOCKER_HOST is the socket forwarded to the host,
    and the Ryuk reaper container must mount the socket path inside the VM.
    """
    in_engine = _podman("info", "--format", "{{.Host.RemoteSocket.Path}}")
    on_host = (
        _podman(
            "machine", "inspect", "--format", "{{.ConnectionInfo.PodmanSocket.Path}}"
        )
        or in_engine
    )
    if on_host and Path(on_host).exists():
        os.environ["DOCKER_HOST"] = f"unix://{on_host}"
        os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", in_engine)


def _use_container_engine() -> None:
    """Pick the engine for testcontainers; an explicit DOCKER_HOST always wins."""
    if "DOCKER_HOST" in os.environ:
        return
    engine = os.environ.get("CONTAINER_ENGINE") or (
        "podman" if shutil.which("podman") else "docker"
    )
    if engine == "podman":
        _use_podman()


def _engine_available() -> bool:
    import docker

    _use_container_engine()
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def docker_required() -> None:
    if not _engine_available():
        pytest.skip(
            "No container engine available (Podman or Docker): "
            "integration tests need testcontainers"
        )


def _container_url[C: DockerContainer](
    make: Callable[[], C], url: Callable[[C], str]
) -> Iterator[str]:
    """Start a container and yield its URL, retrying once if it exits early.

    Under Podman a container occasionally exits right after starting, which
    surfaces as "container did not become running" when its port is read.
    The failed container's logs are attached so the cause is visible.
    """
    for attempt in (1, 2):
        container = make()
        try:
            container.start()
            address = url(container)
        except TimeoutError:
            logs = _logs(container)
            with suppress(Exception):  # it may never have been created
                container.stop()
            if attempt == 2:
                raise
            warnings.warn(
                f"{container.image} exited at start, retrying. Logs:\n{logs}",
                stacklevel=2,
            )
            continue
        try:
            yield address
        finally:
            container.stop()
        return


def _logs(container: DockerContainer) -> str:
    try:
        out, err = container.get_logs()
    except Exception as error:  # the container may already be gone
        return f"<no logs: {error!r}>"
    return (out + err).decode(errors="replace")[-4000:]


def _amqp_url(mq: RabbitMqContainer) -> str:
    host = mq.get_container_host_ip()
    port = mq.get_exposed_port(mq.port)
    return f"amqp://{mq.username}:{mq.password}@{host}:{port}/"


@pytest.fixture(scope="session")
def postgres_url(docker_required: None) -> Iterator[str]:
    yield from _container_url(
        lambda: PostgresContainer(
            "docker.io/library/postgres:16-alpine", driver="asyncpg"
        ),
        lambda pg: pg.get_connection_url(),
    )


@pytest.fixture(scope="session")
def rabbitmq_url(docker_required: None) -> Iterator[str]:
    yield from _container_url(
        lambda: RabbitMqContainer("docker.io/library/rabbitmq:3-management-alpine"),
        _amqp_url,
    )


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
