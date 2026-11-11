"""send_webhook's address check and pinning, with a fake resolver and transport."""

import socket

import httpx
import pytest

from payments.consumer.errors import Unprocessable
from payments.consumer.webhook import DeliveryFailure, send_webhook


def resolver(*addresses: str):
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


async def failing_resolver(host: str, port: int) -> list[str]:
    raise socket.gaierror(-2, "Name or service not known")


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


async def send(
    url: str, recorder: Recorder, resolve, allowed_hosts: tuple[str, ...] = ()
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as client:
        await send_webhook(
            client, url, {"a": 1}, 2, allowed_hosts=allowed_hosts, resolve=resolve
        )


async def test_host_resolving_to_a_blocked_address_is_refused_without_a_request() -> (
    None
):
    recorder = Recorder()
    with pytest.raises(Unprocessable, match="blocked"):
        await send("http://hook.example/p", recorder, resolver("10.0.0.5"))
    assert recorder.requests == []


async def test_one_blocked_address_among_public_ones_refuses_the_host() -> None:
    recorder = Recorder()
    with pytest.raises(Unprocessable):
        await send("http://hook.example/p", recorder, resolver("8.8.8.8", "::1"))
    assert recorder.requests == []


async def test_request_goes_to_the_checked_ip_keeping_host_header() -> None:
    recorder = Recorder()
    await send("http://hook.example:8080/p?x=1", recorder, resolver("93.184.216.34"))
    (request,) = recorder.requests
    assert request.url.host == "93.184.216.34"
    assert request.url.port == 8080
    assert request.url.path == "/p"
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "hook.example:8080"


async def test_https_request_keeps_the_hostname_for_sni_and_certificate() -> None:
    recorder = Recorder()
    await send("https://hook.example/p", recorder, resolver("2606:4700:4700::1111"))
    (request,) = recorder.requests
    assert request.url.host == "2606:4700:4700::1111"
    assert request.headers["host"] == "hook.example"
    assert request.extensions["sni_hostname"] == "hook.example"


async def test_allowlisted_host_is_sent_as_is_without_resolving() -> None:
    recorder = Recorder()
    await send(
        "http://Sink:9000/p", recorder, failing_resolver, allowed_hosts=("sink",)
    )
    (request,) = recorder.requests
    assert request.url.host == "sink"


async def test_dns_failure_is_a_delivery_failure_not_unprocessable() -> None:
    recorder = Recorder()
    with pytest.raises(DeliveryFailure):
        await send("http://hook.example/p", recorder, failing_resolver)
    assert recorder.requests == []


async def test_errors_do_not_show_userinfo_or_query() -> None:
    recorder = Recorder()
    url = "http://user:secret@hook.example/p?token=x"
    with pytest.raises(Unprocessable) as blocked:
        await send(url, recorder, resolver("127.0.0.1"))
    with pytest.raises(DeliveryFailure) as dns:
        await send(url, recorder, failing_resolver)
    for error in (blocked.value, dns.value):
        assert "secret" not in str(error)
        assert "token=x" not in str(error)
