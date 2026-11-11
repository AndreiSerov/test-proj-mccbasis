import httpx
import pytest

from payments.api.app import create_app
from payments.core.settings import Settings

KEY = {"X-API-Key": "test-key"}


@pytest.fixture
async def client() -> httpx.AsyncClient:
    # Docs and /health touch neither Postgres nor RabbitMQ, so dummy URLs are enough.
    settings = Settings(
        database_url="postgresql+asyncpg://unused@localhost/unused",
        rabbitmq_url="amqp://unused@localhost/",
        api_key="test-key",
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_docs_without_the_key_return_401(
    client: httpx.AsyncClient, path: str, headers: dict[str, str]
) -> None:
    assert (await client.get(path, headers=headers)).status_code == 401


async def test_openapi_with_the_key_describes_the_api(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/openapi.json", headers=KEY)

    assert response.status_code == 200
    assert "/api/v1/payments" in response.json()["paths"]


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_docs_pages_with_the_key_render(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path, headers=KEY)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_health_stays_open(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
