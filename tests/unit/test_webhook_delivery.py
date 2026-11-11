"""send_webhook's address check and pinning, with a fake resolver and backend."""

import socket
from typing import Any

import httpcore
import httpx
import pytest

from payments.consumer.errors import Unprocessable
from payments.consumer.webhook import (
    DeliveryFailure,
    PinningTransport,
    send_webhook,
)

OK = b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n"


def resolver(*addresses: str):
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


async def failing_resolver(host: str, port: int) -> list[str]:
    raise socket.gaierror(-2, "Name or service not known")


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, backend: "FakeBackend") -> None:
        self._backend = backend
        self._pending = b""

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        data, self._pending = self._pending, b""
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._backend.requests_seen.append(buffer)
        if buffer.endswith(b"}"):  # the JSON body is the last write of a request
            self._pending = OK

    async def aclose(self) -> None:
        pass

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "FakeStream":
        self._backend.tls_hostnames.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class FakeBackend(httpcore.AsyncNetworkBackend):
    """Records where connections go; every one answers 200."""

    def __init__(self, error: Exception | None = None) -> None:
        self.connects: list[tuple[str, int]] = []
        self.tls_hostnames: list[str | None] = []
        self.requests_seen: list[bytes] = []
        self._error = error

    async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> FakeStream:
        self.connects.append((host, port))
        if self._error is not None:
            raise self._error
        return FakeStream(self)

    async def connect_unix_socket(self, path: str, **kwargs: Any) -> FakeStream:
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        pass


async def send(
    url: str,
    backend: FakeBackend,
    resolve,
    allowed_hosts: tuple[str, ...] = (),
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    if client is not None:
        await send_webhook(
            client, url, {"a": 1}, 2, allowed_hosts=allowed_hosts, resolve=resolve
        )
        return
    async with httpx.AsyncClient(transport=PinningTransport(backend)) as c:
        await send_webhook(
            c, url, {"a": 1}, 2, allowed_hosts=allowed_hosts, resolve=resolve
        )


async def test_blocked_address_is_refused_without_a_connection() -> None:
    backend = FakeBackend()
    with pytest.raises(Unprocessable, match="blocked"):
        await send("http://hook.example/p", backend, resolver("10.0.0.5"))
    assert backend.connects == []


async def test_one_blocked_address_among_public_ones_refuses_the_host() -> None:
    backend = FakeBackend()
    with pytest.raises(Unprocessable):
        await send("http://hook.example/p", backend, resolver("8.8.8.8", "::1"))
    assert backend.connects == []


async def test_connection_goes_to_the_checked_ip_on_the_url_port() -> None:
    backend = FakeBackend()
    await send("http://hook.example:8080/p?x=1", backend, resolver("93.184.216.34"))
    assert backend.connects == [("93.184.216.34", 8080)]
    request = backend.requests_seen[0]
    assert request.startswith(b"POST /p?x=1 HTTP/1.1")
    assert b"Host: hook.example:8080" in request


async def test_https_handshake_uses_the_hostname_not_the_ip() -> None:
    backend = FakeBackend()
    await send("https://hook.example/p", backend, resolver("2606:4700:4700::1111"))
    assert backend.connects == [("2606:4700:4700::1111", 443)]
    assert backend.tls_hostnames == ["hook.example"]


async def test_hosts_sharing_an_ip_do_not_share_a_pooled_connection() -> None:
    backend = FakeBackend()
    async with httpx.AsyncClient(transport=PinningTransport(backend)) as client:
        for host in ("a.example", "b.example", "a.example"):
            await send(
                f"https://{host}/p", backend, resolver("93.184.216.34"), client=client
            )
    # a and b handshake under their own names; the second a reuses its connection
    assert backend.tls_hostnames == ["a.example", "b.example"]
    assert backend.connects == [("93.184.216.34", 443)] * 2


async def test_allowlisted_host_connects_by_name_without_resolving() -> None:
    backend = FakeBackend()
    await send("http://Sink:9000/p", backend, failing_resolver, allowed_hosts=("sink",))
    assert backend.connects == [("sink", 9000)]


async def test_dns_failure_is_a_delivery_failure_not_unprocessable() -> None:
    backend = FakeBackend()
    with pytest.raises(DeliveryFailure):
        await send("http://hook.example/p", backend, failing_resolver)
    assert backend.connects == []


async def test_errors_do_not_show_userinfo_or_query() -> None:
    backend = FakeBackend()
    url = "http://user:secret@hook.example/p?token=x"
    with pytest.raises(Unprocessable) as blocked:
        await send(url, backend, resolver("127.0.0.1"))
    with pytest.raises(DeliveryFailure) as dns:
        await send(url, backend, failing_resolver)
    for error in (blocked.value, dns.value):
        assert "secret" not in str(error)
        assert "token=x" not in str(error)


async def test_transport_errors_show_only_the_exception_type() -> None:
    backend = FakeBackend(
        error=httpcore.ConnectError("refused: user:secret@host?token=x")
    )
    with pytest.raises(DeliveryFailure) as failure:
        await send("http://user:secret@hook.example/p", backend, resolver("8.8.8.8"))
    assert "ConnectError" in str(failure.value)
    assert "secret" not in str(failure.value)
    assert "token=x" not in str(failure.value)


async def test_an_unparseable_url_is_unprocessable_with_a_masked_message() -> None:
    backend = FakeBackend()
    with pytest.raises(Unprocessable) as error:
        await send("http://user:secret@[::1/p", backend, resolver("8.8.8.8"))
    assert "secret" not in str(error.value)
    assert backend.connects == []
