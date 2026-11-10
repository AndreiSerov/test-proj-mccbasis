import httpx

from payments.api.app import create_app
from payments.core.settings import Settings


async def test_health_is_open_and_returns_200() -> None:
    # /health touches neither Postgres nor RabbitMQ, so dummy URLs are enough.
    settings = Settings(
        database_url="postgresql+asyncpg://unused@localhost/unused",
        rabbitmq_url="amqp://unused@localhost/",
        api_key="test-key",
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
