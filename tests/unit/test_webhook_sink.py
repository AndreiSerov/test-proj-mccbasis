"""The demo webhook sink: logs every request, answers 200, or 500 on `?fail=1`."""

import logging

import httpx
import pytest

from payments.demo.webhook_sink import create_app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://sink") as c:
        yield c


def _sink_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "webhook_sink"]


async def test_answers_200_and_logs_method_path_query_and_json_body(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = await client.post("/hook?a=b", json={"payment_id": "p-1"})

    assert response.status_code == 200
    [record] = _sink_records(caplog)
    assert record.__dict__["method"] == "POST"
    assert record.__dict__["path"] == "/hook"
    assert record.__dict__["query"] == "a=b"
    assert record.__dict__["body"] == {"payment_id": "p-1"}


async def test_fail_query_answers_500_and_is_still_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = await client.post("/hook?fail=1", json={"payment_id": "p-2"})

    assert response.status_code == 500
    [record] = _sink_records(caplog)
    assert record.__dict__["body"] == {"payment_id": "p-2"}
