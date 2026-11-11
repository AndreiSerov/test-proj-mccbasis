import asyncio
import contextvars
import socket
from collections.abc import Awaitable, Callable, Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import httpcore
import httpx

from payments.consumer.errors import Unprocessable
from payments.db.models import Payment
from payments.domain.webhook_url import is_allowed_host, is_blocked_address, mask_url


class DeliveryFailure(Exception):
    """A technical error while handling a message (Webhook non-2xx or timeout)."""


def _iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a `Z` suffix, the same form the API returns."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def webhook_payload(payment: Payment) -> dict[str, Any]:
    if payment.processed_at is None:
        raise ValueError(f"payment {payment.id} has no processed_at")
    return {
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "processed_at": _iso_utc(payment.processed_at),
        "metadata": payment.metadata_,
    }


Resolve = Callable[[str, int], Awaitable[list[str]]]
"""`(host, port)` -> every address the name resolves to."""


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve without blocking the event loop."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


_pinned: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "webhook_pinned_address", default=None
)
"""`(hostname, checked IP)` for the request in flight in this task."""


@contextmanager
def _pin(host: str, address: str) -> Iterator[None]:
    token = _pinned.set((host.lower(), address))
    try:
        yield
    finally:
        _pinned.reset(token)


class _PinningBackend(httpcore.AsyncNetworkBackend):
    """Connects to the checked IP when the connection is for the pinned hostname.

    The request keeps the hostname in its URL, so httpcore pools connections by
    name and TLS uses the name for SNI and certificate verification; only the
    TCP connect is redirected. httpcore opens a connection in the task that sent
    the request, so the context variable set by `send_webhook` is the right one.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self, host: str, port: int, *args: Any, **kwargs: Any
    ) -> httpcore.AsyncNetworkStream:
        pinned = _pinned.get()
        if pinned is not None and pinned[0] == host.lower():
            host = pinned[1]
        return await self._inner.connect_tcp(host, port, *args, **kwargs)

    async def connect_unix_socket(
        self, *args: Any, **kwargs: Any
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(*args, **kwargs)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _default_backend() -> httpcore.AsyncNetworkBackend:
    # AnyIOBackend is an AsyncNetworkBackend; httpcore's stubs do not say so.
    return cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())


class PinningTransport(httpx.AsyncHTTPTransport):
    """An httpx transport whose connections go to the IP `send_webhook` checked.

    httpx offers no hook for the network backend, so the connection pool is
    rebuilt with one (httpcore's public `network_backend` argument).
    """

    def __init__(self, network_backend: httpcore.AsyncNetworkBackend | None = None):
        super().__init__()
        limits = httpx.Limits()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_PinningBackend(network_backend or _default_backend()),
        )


async def send_webhook(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    allowed_hosts: Collection[str] = (),
    resolve: Resolve = resolve_host,
) -> None:
    """POST the Webhook; anything but a 2xx in `timeout` seconds in total fails.

    httpx timeouts apply per phase (a slow trickle never trips a read timeout),
    so the whole request also runs under one asyncio deadline.

    Unless its host is allowlisted, the host is resolved here and refused
    (Unprocessable) if any address is a Blocked address; the connection then
    goes to the checked IP, so a DNS answer that changes later cannot redirect
    it. This needs `client` to use a `PinningTransport`. The URL keeps the
    hostname, so pooling, SNI and certificate verification use the name.
    Every message shows the URL masked.
    """
    shown = mask_url(url)
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL as error:
        raise Unprocessable(f"webhook to {shown} refused: invalid URL") from error
    try:
        async with asyncio.timeout(timeout):
            if is_allowed_host(target.host, allowed_hosts):
                response = await client.post(target, json=payload, timeout=timeout)
            else:
                address = await _checked_address(target, resolve, shown)
                with _pin(target.host, address):
                    response = await client.post(target, json=payload, timeout=timeout)
    except socket.gaierror as error:
        raise DeliveryFailure(
            f"webhook to {shown}: cannot resolve host: {error}"
        ) from error
    except (httpx.HTTPError, TimeoutError) as error:
        # Only the type: the message could carry the URL, userinfo included.
        raise DeliveryFailure(
            f"webhook to {shown} failed: {type(error).__name__}"
        ) from error
    if not response.is_success:
        raise DeliveryFailure(f"webhook to {shown} returned {response.status_code}")


async def _checked_address(target: httpx.URL, resolve: Resolve, shown: str) -> str:
    port = target.port or (443 if target.scheme == "https" else 80)
    addresses = await resolve(target.host, port)
    blocked = [a for a in addresses if is_blocked_address(a)]
    if blocked or not addresses:
        raise Unprocessable(
            f"webhook to {shown} refused: host resolves to a blocked address"
            f" ({', '.join(blocked) or 'none'})"
        )
    return addresses[0]
